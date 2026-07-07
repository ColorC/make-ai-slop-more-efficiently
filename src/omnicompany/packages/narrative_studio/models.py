"""叙事引擎格式契约(P0 已定稿)。

设计依据 07-叙事引擎格式与功能设计.md 第二节。要点:
- 单一真源:Project 持有全部载体,所有视图都是它的投影。
- 逐级放大=字段链(Summary 一句/一段/完整)。
- 完成度=空字段 + status(todo/tocomplete/done)+ 汇总。
- 先语义后文风:Scene 只引用 line_ref,真实成文在 ProseLine。
- 分支=显式 Connection(按稳定 id);条件/效果/触发=Expr 列表(轻反应性)。
- 伏笔=Tag(不另立格式)。
- 三层叙事=RevealLayer + meta_progress(全局进度,一等存档量)。

pydantic v1/v2 兼容写法。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 基础枚举 / 公共原语
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    todo = "todo"
    tocomplete = "tocomplete"
    done = "done"


class VarType(str, Enum):
    bool = "bool"
    int = "int"
    string = "string"


class NodeType(str, Enum):
    scene = "scene"
    hub = "hub"
    condition = "condition"
    jump = "jump"
    ending = "ending"


class RevealOrder(str, Enum):
    surface = "surface"       # 表层
    midpoint = "midpoint"     # 中段揭示
    true_end = "true_end"     # True End 揭示


CompareOp = ("==", "!=", ">", ">=", "<", "<=")
AssignOp = ("set", "=", "+=", "-=")


class Expr(BaseModel):
    """单条条件或效果。

    条件: {var: "攻略对象.好感度", op: ">=", value: 50}
    效果: {var: "枫钟.了解程度", op: "set", value: 1}
    var 形如 "namespace.name";meta 进度量用 namespace="meta"。
    """

    var: str
    op: str
    value: Union[bool, int, float, str, None] = None


class Provenance(BaseModel):
    """回指来源讨论稿,可追溯。"""

    source: Optional[str] = None      # 如 "seeds/00-core-concept.md"
    note: Optional[str] = None


class Summary(BaseModel):
    """逐级放大字段链(Manuskript 雪花)。每级可独立填。"""

    sentence: Optional[str] = None
    paragraph: Optional[str] = None
    full: Optional[str] = None


# --------------------------------------------------------------------------- #
# 立意(D1) + 揭示层
# --------------------------------------------------------------------------- #
class StoryformThroughline(BaseModel):
    perspective: str   # they|i|you|we
    domain: Optional[str] = None
    note: Optional[str] = None


class Storyform(BaseModel):
    """可选脚手架(Dramatica NCP 风格,据公开文档,非强制)。"""

    throughlines: List[StoryformThroughline] = Field(default_factory=list)
    dynamics: List[Dict[str, Any]] = Field(default_factory=list)
    storypoints: Dict[str, Any] = Field(default_factory=dict)


class Premise(BaseModel):
    """立意 = 真源之首,冻结(但可被揭示层重写)。"""

    proposition: Optional[str] = None        # 因果命题
    controlling_ideas: List[str] = Field(default_factory=list)  # 结局要证成的理念(可多条)
    stance: Optional[str] = None             # 隐含立场
    locked: bool = False
    storyform: Optional[Storyform] = None
    provenance: Optional[Provenance] = None


class RevealLayer(BaseModel):
    """跨路线元结构:完成 N 条路线后整部解读框架翻转。

    不是 ending(非终点),也不是 scene.reveal_order(非单场)。
    """

    id: str
    order: RevealOrder
    title: Optional[str] = None
    trigger: List[Expr] = Field(default_factory=list)   # 读 meta.* 全局进度
    rewrites: Optional[str] = None      # 翻转后 controlling_idea/stance 如何被重读
    rewrites_controlling_idea: Optional[str] = None
    status: Status = Status.todo
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 世界 / 角色 / 关系
# --------------------------------------------------------------------------- #
class WorldNode(BaseModel):
    """世界设定层级树(Manuskript OPML)。"""

    id: str
    name: str
    description: Optional[str] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    children: List["WorldNode"] = Field(default_factory=list)
    provenance: Optional[Provenance] = None


class CharacterArc(BaseModel):
    """人物弧四元(Truby/Save the Cat 一系)。"""

    want: Optional[str] = None    # 外在欲求
    need: Optional[str] = None    # 内在需要
    wound: Optional[str] = None   # 创伤
    lie: Optional[str] = None     # 误信


class DossierField(BaseModel):
    """角色档案某一维(bibisco 采访:逐题或整段)。"""

    dimension: str
    mode: str = "freetext"   # interview|freetext
    questions: List[Dict[str, Any]] = Field(default_factory=list)  # {q, a}
    freetext: Optional[str] = None
    status: Status = Status.todo


class Character(BaseModel):
    id: str
    name: str
    importance: str = "secondary"   # main|secondary|group
    color: Optional[str] = None
    image: Optional[str] = None
    arc: CharacterArc = Field(default_factory=CharacterArc)
    summary: Summary = Field(default_factory=Summary)
    dossier: List[DossierField] = Field(default_factory=list)
    facts: List[Dict[str, Any]] = Field(default_factory=list)   # 可被利用的信息等
    secret: Optional[str] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    status: Status = Status.todo
    provenance: Optional[Provenance] = None


class Relationship(BaseModel):
    id: str
    a: str   # character id
    b: str
    nature: Optional[str] = None
    label: Optional[str] = None
    projection: Optional[str] = None   # Vilo 的欲望投影(关系矩阵)
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 数值 / 状态
# --------------------------------------------------------------------------- #
class Variable(BaseModel):
    namespace: str
    name: str
    type: VarType = VarType.int
    default: Union[bool, int, float, str, None] = None
    description: Optional[str] = None
    counter: bool = True   # True=累积 counter / False=一次性 flag

    @property
    def key(self) -> str:
        return f"{self.namespace}.{self.name}"


class StatBlock(BaseModel):
    """可复用属性组(articy Feature):套到每个攻略对象。"""

    name: str
    fields: List[str] = Field(default_factory=list)   # 变量名(不含 namespace)
    applies_to: List[str] = Field(default_factory=list)  # character ids


class MetaProgress(BaseModel):
    """全局/跨周目进度,一等存档量(共识8 落成实体)。

    实际值在 playthrough 运行时维护;这里声明它有哪些可被引用的维度。
    """

    fields: List[Variable] = Field(default_factory=list)


class Pressure(BaseModel):
    """vilo 压力卡:名称 + 自由文本表现。

    早先把"衰变/抵消/副作用/阈值分支"做成 5 个强类型反应性子字段,属过度工程
    (2026-06-27 用户:简化);反应性机制改走通用 变量 + Connection.condition,
    具体行为(衰变/抵消/副作用)写进 manifest 自由文本即可。
    """

    id: str
    name: str
    manifest: Optional[str] = None     # 自由文本:表现/衰变/抵消/副作用等都写这
    provenance: Optional[Provenance] = None


class FailureLevel(BaseModel):
    """失败分层(17 第5节):带前置级联 + 预兆。"""

    level: str   # 轻|中|重|终局
    manifest: Optional[str] = None
    prereq_chain: List[str] = Field(default_factory=list)
    warning: Optional[str] = None
    # motivated(失败是否"师出有名")属场景级 D6 评价,不在失败分层结构里(2026-06-27 简化移除)


# --------------------------------------------------------------------------- #
# 结构 / 大纲 / 节奏
# --------------------------------------------------------------------------- #
class Beat(BaseModel):
    """节拍(可分 幕→章→场 任意层级)。

    2026-07-04 大纲正式记录结构(作者:大纲不适合纯列表观看,要结构图/散卡):
    - 顶层 beat(parent=None)= 大纲的"段"(共通线/单方面认识/…),position 定序;
    - lane = 所属故事线(storyline id),段×线 = 散卡矩阵的格;None=共通/全局;
    - edges = 图边(后继 beat id)——"大纲是图不是线"(wiki/05 待细化③);
    - authority = 认可状态直接标在内容上(台账教训:状态与内容分离=再污染温床)。
    """

    id: str
    parent: Optional[str] = None   # None=顶层(=大纲的"段")
    title: Optional[str] = None
    function: Optional[str] = None
    summary: Summary = Field(default_factory=Summary)
    position: int = 0
    status: Status = Status.todo
    lane: Optional[str] = None     # storyline id;None=共通/全局
    edges: List[str] = Field(default_factory=list)   # 后继 beat ids(DAG 边)
    authority: Optional[str] = None  # "author"=作者口述/手稿 | "ai-draft"=AI 拟待认
    provenance: Optional[Provenance] = None


class StoryLine(BaseModel):
    """故事线/线索行(Plottr 时间线 y 轴)。"""

    id: str
    title: str
    color: Optional[str] = None
    character_id: Optional[str] = None


class Arc(BaseModel):
    """情感弧 / 张力弧:有序的命名锚点(如 孤独的声音→好奇→追踪→…)。

    早先做成 ArcPoint{at, value: float} 数值曲线,属"假精确"过度工程
    (2026-06-27 用户:简化);改回 wiki/07-v0 实际用法——一串有序的命名节拍。
    """

    emotional: List[str] = Field(default_factory=list)   # 情感弧:有序命名锚点
    tension: List[str] = Field(default_factory=list)     # 张力弧:有序命名锚点


class PacingMarker(BaseModel):
    kind: str          # phase|day
    name: str
    core_event: Optional[str] = None
    main_pressure: Optional[str] = None
    position: int = 0


# --------------------------------------------------------------------------- #
# 路线 / 分支(节点图)
# --------------------------------------------------------------------------- #
class Node(BaseModel):
    id: str
    type: NodeType = NodeType.scene
    title: Optional[str] = None
    route: Optional[str] = None    # 所属路线(可选分组)
    x: float = 0.0                 # 布局坐标,逻辑无关
    y: float = 0.0
    # condition 型节点用 condition;jump 型用 target
    condition: List[Expr] = Field(default_factory=list)
    target: Optional[str] = None
    provenance: Optional[Provenance] = None


class Connection(BaseModel):
    id: str
    source: str        # node id
    target: str        # node id
    condition: List[Expr] = Field(default_factory=list)   # 选项可见性/分支条件
    effects: List[Expr] = Field(default_factory=list)      # 选后果
    label: Optional[str] = None


class Ending(BaseModel):
    node_id: str
    name: str
    trigger: List[Expr] = Field(default_factory=list)   # 可读 meta.* 跨路线门控
    priority: int = 0   # 多结局同时满足时取高
    color: Optional[str] = None
    emotional_color: Optional[str] = None
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 场景(枢纽:R2 单元 + R3 语义规格)
# --------------------------------------------------------------------------- #
class SceneLinks(BaseModel):
    pov: Optional[str] = None
    characters: List[str] = Field(default_factory=list)
    places: List[str] = Field(default_factory=list)   # world node ids
    lines: List[str] = Field(default_factory=list)     # storyline ids
    time: Optional[str] = None


class Choice(BaseModel):
    label: str
    condition: List[Expr] = Field(default_factory=list)
    effects: List[Expr] = Field(default_factory=list)
    target: Optional[str] = None   # node id


class Causality(BaseModel):
    why_now: Optional[str] = None
    why_inevitable: Optional[str] = None


class ValueShift(BaseModel):
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None

    model_config = {"populate_by_name": True}


class Intent(BaseModel):
    """目标情绪/震撼:喂给未来 6 维评价层(D3/D6),当前不校验。"""

    emotion: Optional[str] = None
    punch: Optional[str] = None
    resonance: Optional[str] = None
    afterglow: Optional[str] = None


class RenderConstraints(BaseModel):
    """给成文层的约束(D2)。"""

    distance: Optional[str] = None
    focalization: Optional[str] = None
    voices: List[str] = Field(default_factory=list)
    reveal_order: Optional[str] = None
    show_not_tell: List[str] = Field(default_factory=list)


class Scene(BaseModel):
    id: str
    node_ref: Optional[str] = None
    beat: Optional[str] = None   # 直接归属的 beat id(优先);为空时回退 node_ref→node.route
    title: Optional[str] = None
    intent_summary: Optional[str] = None   # 高层意图一句话
    links: SceneLinks = Field(default_factory=SceneLinks)
    preconditions: List[Expr] = Field(default_factory=list)
    effects: List[Expr] = Field(default_factory=list)
    choices: List[Choice] = Field(default_factory=list)
    summary: Summary = Field(default_factory=Summary)
    objective_events: List[str] = Field(default_factory=list)   # 无文风事实
    causality: Causality = Field(default_factory=Causality)
    value_shift: ValueShift = Field(default_factory=ValueShift)
    intent: Intent = Field(default_factory=Intent)
    render_constraints: RenderConstraints = Field(default_factory=RenderConstraints)
    line_refs: List[str] = Field(default_factory=list)   # 指向 ProseLine.id
    tags: List[str] = Field(default_factory=list)         # tag ids
    serves_ideas: List[str] = Field(default_factory=list)  # 声明服务于哪条 controlling_idea
    status: Status = Status.todo
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 成文(R5,文本层)+ 文风
# --------------------------------------------------------------------------- #
class ProseRevision(BaseModel):
    text: str
    at: Optional[str] = None   # 时间戳(由调用方注入,模型内不取时间)
    note: Optional[str] = None


class ProseLine(BaseModel):
    id: str
    scene_ref: Optional[str] = None
    speaker: Optional[str] = None
    voice: Optional[str] = None
    text: Optional[str] = None   # 可空(先语义后文风)
    tags: List[str] = Field(default_factory=list)   # 行级标签(伏笔/线索亦可挂行)
    revisions: List[ProseRevision] = Field(default_factory=list)
    status: Status = Status.todo


class Voice(BaseModel):
    id: str
    register_id: Optional[str] = None
    syntax: Optional[str] = None
    lexicon: Optional[str] = None
    taboos: Optional[str] = None


class Register(BaseModel):
    id: str
    rule: Optional[str] = None


class StyleMatrixEntry(BaseModel):
    emotion: Optional[str] = None
    scene_type: Optional[str] = None
    register_id: Optional[str] = None
    style_config: Optional[str] = None


# --------------------------------------------------------------------------- #
# 标签 / 伏笔 / 线索
# --------------------------------------------------------------------------- #
class Tag(BaseModel):
    id: str
    name: str
    color: Optional[str] = None
    kind: str = "tag"   # tag|foreshadow|clue|theme


# --------------------------------------------------------------------------- #
# 全局便签
# --------------------------------------------------------------------------- #
class Note(BaseModel):
    id: str
    text: str = ""
    at: Optional[str] = None


# --------------------------------------------------------------------------- #
# 圈选评论(创作者反馈收件箱) —— 吸收 vilo wiki/comments;复用 anchor 评论范式
# --------------------------------------------------------------------------- #
class Comment(BaseModel):
    id: str
    target: Optional[str] = None      # 被评论的实体(carrier:id)或游戏文本 id
    anchor: Optional[str] = None      # 圈选锚点(文本片段/选择器)
    body: str = ""
    author: Optional[str] = None
    resolved: bool = False
    at: Optional[str] = None


# --------------------------------------------------------------------------- #
# 落地层:游戏内具体文本(卡片/事件/标签/wiki) —— 游戏本身的内容,亦在游戏内 wiki 显示
# --------------------------------------------------------------------------- #
class GameTextChoice(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    body: Optional[str] = None


class GameText(BaseModel):
    """一条游戏内具体文本(= vilo wiki 的 card/event/tag/wiki 词条)。

    这是"最后真源的又一个显示,但实际上是游戏本身的内容"。
    """

    id: str
    text_type: str = "card"          # card | event | tag | wiki
    title: Optional[str] = None
    category: Optional[str] = None    # 如 event.news
    host: Optional[str] = None        # 事件挂载的行动入口
    body: Optional[str] = None        # 文案 / 正文
    choices: List[GameTextChoice] = Field(default_factory=list)
    art: Optional[str] = None
    art_status: Optional[str] = None  # 如 generated-unreviewed
    annotations: Optional[str] = None  # 创作者批注
    related: List[str] = Field(default_factory=list)
    is_draft: bool = False            # True=草稿(在 wiki/drafts/,未转正式)
    status: Status = Status.todo
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 否决案归档:被取代/被否决的内容,记录但绝不作真源/基础
# --------------------------------------------------------------------------- #
class RejectedItem(BaseModel):
    id: str
    area: str                         # 内容域(具体文风/叙事结构/情节/具体文本/世界设定…)
    title: str
    verdict: str = "rejected"         # rejected | superseded
    reason: Optional[str] = None      # 为何否决/被谁取代
    source: Optional[str] = None      # 来源文件
    excerpt: Optional[str] = None     # 摘录(供查证,不当基础)


# --------------------------------------------------------------------------- #
# 受众与预期管理(D6,叙事指导层) —— 用户已认可的"受众讨论/方向"
# --------------------------------------------------------------------------- #
class AudienceSegment(BaseModel):
    name: str
    note: Optional[str] = None


class Audience(BaseModel):
    segments: List[AudienceSegment] = Field(default_factory=list)
    stance: Optional[str] = None              # 受众基调(如:不羞辱"想被爱",不把女性欲望写成病态)
    expectations: List[str] = Field(default_factory=list)   # 期待管理(promise→payoff)
    resonance_targets: List[str] = Field(default_factory=list)  # 共鸣/余韵目标
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 背景 / 思考(叙事指导层) —— 世界背景 + 设计理念/取舍 + 待定问题(吸收旧 wiki drafts/便签的"想")
# --------------------------------------------------------------------------- #
class Background(BaseModel):
    thinking: Optional[str] = None            # 设计理念/取舍/思考
    world_notes: Optional[str] = None         # 世界观背景
    open_questions: List[str] = Field(default_factory=list)  # 待定问题
    provenance: Optional[Provenance] = None


# --------------------------------------------------------------------------- #
# 顶层 Project(单一真源)
# --------------------------------------------------------------------------- #
class ProjectMeta(BaseModel):
    id: str
    name: str
    version: str = "v1"
    supersedes: List[str] = Field(default_factory=list)
    coexists_with: List[str] = Field(default_factory=list)
    aesthetic: Optional[str] = None
    description: Optional[str] = None


class Project(BaseModel):
    """单一真源:持有全部载体。所有视图皆为它的投影。"""

    meta: ProjectMeta

    premise: Premise = Field(default_factory=Premise)
    reveal_layers: List[RevealLayer] = Field(default_factory=list)

    world: List[WorldNode] = Field(default_factory=list)
    characters: List[Character] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)

    variables: List[Variable] = Field(default_factory=list)
    stat_blocks: List[StatBlock] = Field(default_factory=list)
    meta_progress: MetaProgress = Field(default_factory=MetaProgress)
    pressures: List[Pressure] = Field(default_factory=list)
    failure_levels: List[FailureLevel] = Field(default_factory=list)

    beats: List[Beat] = Field(default_factory=list)
    storylines: List[StoryLine] = Field(default_factory=list)
    arc: Arc = Field(default_factory=Arc)
    pacing: List[PacingMarker] = Field(default_factory=list)

    nodes: List[Node] = Field(default_factory=list)
    connections: List[Connection] = Field(default_factory=list)
    endings: List[Ending] = Field(default_factory=list)

    scenes: List[Scene] = Field(default_factory=list)

    prose_lines: List[ProseLine] = Field(default_factory=list)
    voices: List[Voice] = Field(default_factory=list)
    registers: List[Register] = Field(default_factory=list)
    style_matrix: List[StyleMatrixEntry] = Field(default_factory=list)

    tags: List[Tag] = Field(default_factory=list)
    notes: List[Note] = Field(default_factory=list)

    # 叙事指导层新增:受众与预期管理 + 背景/思考
    audience: Audience = Field(default_factory=Audience)
    background: Background = Field(default_factory=Background)
    # 落地层:游戏内具体文本(卡片/事件/标签/wiki)= 游戏本身内容
    game_texts: List[GameText] = Field(default_factory=list)
    # 否决案归档:被取代/被否决内容,记录但不作真源
    rejected_archive: List[RejectedItem] = Field(default_factory=list)
    # 圈选评论收件箱(创作者反馈)
    comments: List[Comment] = Field(default_factory=list)


WorldNode.model_rebuild() if hasattr(WorldNode, "model_rebuild") else WorldNode.update_forward_refs()
