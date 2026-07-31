from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PublicTagDefinition:
    """One stable, reader-facing index term."""

    tag: str
    facet: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "facet": self.facet,
            "description": self.description,
        }


def _definition(tag: str, facet: str, description: str) -> PublicTagDefinition:
    return PublicTagDefinition(tag=tag, facet=facet, description=description)


_PUBLIC_TAG_SCOPES = (
    {
        "scope": "game",
        "label": "游戏标签",
        "purpose": "按品类、题材、平台和长期节奏聚合游戏。",
        "examples": ["SLG", "三国", "移动端", "长期经营"],
    },
    {
        "scope": "play",
        "label": "玩法标签",
        "purpose": "跨游戏比较独立系统、核心操作、稳定规则和结果。",
        "examples": ["城市建设", "建筑升级", "资源门槛", "二次确认"],
    },
    {
        "scope": "screen",
        "label": "界面标签",
        "purpose": "识别画面身份、组件模式和稳定可见状态。",
        "examples": ["主城", "邮件列表", "确认弹窗", "可领取"],
    },
    {
        "scope": "feedback",
        "label": "反馈标签",
        "purpose": "聚合同一种玩家问题或体验主题。",
        "examples": ["资源墙", "理解成本", "时间门槛", "搭配深度"],
    },
    {
        "scope": "demo",
        "label": "Demo 标签",
        "purpose": "说明复现覆盖的玩家能力，不暴露实现技术。",
        "examples": ["镜头操作", "烹饪", "阵容配置"],
    },
)


# Public play tags answer one question: which other play pages should this page be
# comparable with? Concrete observations, evidence maturity and agent operations live
# in their own structured fields and are deliberately excluded here.
_PLAY_TAG_DEFINITIONS = (
    _definition("活动", "系统", "限时、周期或阶段式活动内容。"),
    _definition("任务", "系统", "以目标条件和完成回报组织玩家行动。"),
    _definition("章节任务", "系统", "按章节编排目标、引导与进度回报。"),
    _definition("奖励", "系统", "向玩家发放资源、道具或进度收益。"),
    _definition("签到", "系统", "按自然日或连续周期登记并发放回报。"),
    _definition("日历", "界面模式", "按日期展示进度、事件或奖励安排。"),
    _definition("同盟", "系统", "玩家加入并共同经营的组织系统。"),
    _definition("组织管理", "系统", "组织身份、成员、公共资产和治理信息。"),
    _definition("城池管理", "系统", "组织或玩家对城池对象进行查询与管理。"),
    _definition("排行榜", "系统", "按明确口径比较玩家或组织表现。"),
    _definition("成就", "系统", "以长期目标记录完成度并提供回报。"),
    _definition("日程", "系统", "按时点安排、提醒和追踪事件。"),
    _definition("城市建设", "系统", "建设、选择和经营城市内建筑。"),
    _definition("建筑升级", "系统", "投入条件或资源提升建筑等级与能力。"),
    _definition("征兵", "系统", "生产、储备或恢复可用于部队的兵员。"),
    _definition("部队编成", "系统", "组织部队实例及其成员位置。"),
    _definition("阵型", "系统", "选择队形并改变部队加成或战斗表现。"),
    _definition("阵容配置", "系统", "选择角色并组合成可使用的队伍方案。"),
    _definition("角色养成", "系统", "提升角色等级、属性、技能或装备。"),
    _definition("属性分配", "系统", "把可用点数配置到角色属性。"),
    _definition("技能养成", "系统", "学习、升级或配置角色与部队技能。"),
    _definition("装备养成", "系统", "查看、获取或强化角色装备。"),
    _definition("战法搭配", "系统", "围绕队伍或角色选择并组合战法。"),
    _definition("土地经营", "系统", "发现、占有、开垦和利用土地。"),
    _definition("地图探索", "系统", "在世界或区域地图中发现和定位对象。"),
    _definition("战斗", "系统", "进入战斗并产生胜负或兵力变化。"),
    _definition("关卡推进", "系统", "选择关卡、挑战并推进关卡进度。"),
    _definition("离线收益", "系统", "按离线或挂机时长累积可领取收益。"),
    _definition("邮件", "系统", "接收、分类、阅读并处理游戏内消息。"),
    _definition("交易", "系统", "以报价、货币或资源完成交换。"),
    _definition("商店", "系统", "浏览固定或周期刷新的商品与报价。"),
    _definition("资源生产", "系统", "持续产生并汇总游戏资源。"),
    _definition("资源管理", "系统", "查看、分配、储存和消耗资源。"),
    _definition("新手引导", "系统", "引导新玩家理解入口、目标与基础循环。"),
    _definition("生存", "系统", "维持生命、饥饿、安全或环境适应。"),
    _definition("制作", "系统", "把材料加工为可使用对象。"),
    _definition("烹饪", "系统", "把食材加工为可食用或有增益的产物。"),
    _definition("红点", "系统", "用注意力标记提示未处理内容或可行动项。"),
    _definition("模式选择", "导航", "在多个玩法模式之间识别并选择入口。"),
    _definition("玩法入口", "导航", "承担进入独立玩法或业务系统的导航。"),
    _definition("跨系统引导", "导航", "串联多个独立系统以完成同一玩家目标。"),
    _definition("搜索", "操作", "用文本条件定位目标对象。"),
    _definition("筛选", "操作", "用结构化条件缩小对象集合。"),
    _definition("分类浏览", "操作", "在类别或页签之间浏览内容。"),
    _definition("阅读", "操作", "打开并消费消息、说明或公告内容。"),
    _definition("编队", "操作", "创建或调整队伍成员组合。"),
    _definition("出征", "操作", "派出部队前往地图目标。"),
    _definition("占领", "操作", "通过规则取得地图对象的控制权。"),
    _definition("开垦", "操作", "把已拥有土地转化为更高等级或产出状态。"),
    _definition("补兵", "操作", "把预备兵分配给未满兵力的部队。"),
    _definition("学习", "操作", "解锁或掌握新的技能、战法或知识。"),
    _definition("升级", "操作", "提升对象等级并获得对应能力变化。"),
    _definition("补签", "操作", "补登记此前未完成的签到日期。"),
    _definition("方案预览", "操作", "提交前查看方案及其预期变化。"),
    _definition("推荐方案", "操作", "由系统提供可供比较或采用的配置方案。"),
    _definition("奖励领取", "操作", "由玩家主动领取已经产生的奖励。"),
    _definition("奖励结算", "结果", "展示任务或活动奖励的结算结果。"),
    _definition("资源结算", "结果", "展示资源收益、消耗或到账结果。"),
    _definition("战斗结算", "结果", "展示战斗结束后的胜负、损耗与收益。"),
    _definition("胜负结算", "结果", "同时覆盖胜利和失败两类终局反馈。"),
    _definition("首占奖励", "规则", "首次占有符合条件的对象时提供奖励。"),
    _definition("成长目标", "规则", "以成长里程碑组织任务或奖励。"),
    _definition("手动领取", "规则", "奖励不会自动入账，需要玩家明确领取。"),
    _definition("共享进度", "规则", "进度由多个玩家或组织成员共同贡献。"),
    _definition("时间门槛", "规则", "开放、结算或收益受时间条件限制。"),
    _definition("章节门槛", "规则", "操作或内容开放受章节进度限制。"),
    _definition("资源门槛", "规则", "操作能否提交取决于资源是否满足。"),
    _definition("二次确认", "规则", "高影响操作在最终提交前还有确认层。"),
    _definition("身份授权", "规则", "操作或信息范围取决于玩家身份与权限。"),
    _definition("所有权", "规则", "对象行为与结果取决于归属关系。"),
    _definition("成员槽位", "规则", "队伍成员受固定位置或数量限制。"),
    _definition("属性加成", "规则", "配置或状态会改变对象属性。"),
    _definition("战损", "规则", "战斗会造成兵力或单位损耗。"),
    _definition("失败恢复", "规则", "失败后存在可观察的恢复路径或后续状态。"),
    _definition("收益上限", "规则", "累计收益存在容量或时长上限。"),
    _definition("时效奖励", "规则", "奖励必须在规定期限内处理。"),
    _definition("自动补充", "规则", "系统可按规则自动补足资源或兵力。"),
    _definition("容量上限", "规则", "资源存量受到最大容量限制。"),
    _definition("溢出处理", "规则", "资源超过容量后按明确规则处理。"),
    _definition("资源消耗", "规则", "操作会扣除明确的游戏资源。"),
    _definition("兵力分配", "规则", "可用兵员在一个或多个部队间分配。"),
    _definition("资源账本", "解释", "对资源来源、变化和结果进行分账说明。"),
    _definition("来源追踪", "解释", "说明总量或结果由哪些来源贡献。"),
    _definition("产量总览", "解释", "汇总展示多个来源的生产速度与总量。"),
    _definition("注意力引导", "解释", "说明界面如何把玩家注意力导向待办内容。"),
    _definition("原因归因", "解释", "区分提示或数值变化背后的真实原因。"),
    _definition("未读状态", "状态", "内容尚未被玩家阅读或处理。"),
    _definition("已领取状态", "状态", "奖励已经领取且界面完成回写。"),
    _definition("等级成长", "状态", "对象等级随经营或投入发生提升。"),
    _definition("属性查看", "界面模式", "集中展示角色或对象属性。"),
    _definition("角色列表", "界面模式", "以列表方式浏览并选择角色。"),
    _definition("角色详情", "界面模式", "查看单个角色身份、属性和养成入口。"),
    _definition("报价", "界面模式", "提交交易前展示价格、数量和交换条件。"),
    _definition("战前决策", "界面模式", "进入战斗前选择关卡、队伍或行动方案。"),
    _definition("饥饿管理", "规则", "食物与饥饿值共同影响生存状态。"),
    _definition("昼夜循环", "规则", "昼夜变化影响环境、风险或可执行行动。"),
    _definition("预备兵", "资源", "可分配给部队的兵员储备。"),
)

PLAY_TAG_DEFINITIONS = {item.tag: item for item in _PLAY_TAG_DEFINITIONS}

_FORBIDDEN_EXACT_TAGS = {
    "Day4",
    "ProtoWorld",
    "public partial",
    "固定世界 benchmark",
    "后继观察",
    "恢复待证",
    "局部证据",
    "对象验真",
    "独立快照",
    "独立运行",
    "独立运行拓扑",
    "交易隔离",
    "仅供参考",
    "路由纠错",
    "目标效果守卫",
    "未选队负例",
    "一键应用待证",
    "语义纠错",
    "状态投影",
    "副作用护栏",
    "安全返回",
    "安全退出",
    "零编成写入",
    "零事实候选",
}
_INSTANCE_PATTERNS = (
    re.compile(r"\d+\s*[→➜⟶]\s*\d+"),
    re.compile(r"(?i)^day\s*\d+$"),
    re.compile(r"\b\d+\s*/\s*\d+\b"),
    re.compile(r"(?i)operationmemory"),
    re.compile(r"(?i)public\s+partial"),
    re.compile(r"(?i)benchmark"),
)
_SCREEN_INSTANCE_OR_WORKFLOW_PATTERNS = (
    *_INSTANCE_PATTERNS,
    re.compile(r"\d"),
    re.compile(r"第[一二三四五六七八九十百]+(?:章|日|天|夜)"),
    re.compile(r"[一二三四五六七八九十两]+(?:级|档|处|项|章|天|日)(?:以上|以下)?"),
    re.compile(
        r"候选|待证|负例|纠错|确证|错配|旧坐标|"
        r"安全(?:退出|返回|边界)|独立运行|不归属|非直接|"
        r"业务身份|路由驾驶舱|模板变体|专属入口锚点"
    ),
)


def public_play_tag_issues(tags: object) -> list[str]:
    """Return hard publication errors without requiring fixture tags in the registry."""

    if not isinstance(tags, list):
        return ["play.tags must be a list"]
    if not tags:
        return ["play.tags must contain at least one public index term"]
    if len(tags) > 6:
        return ["play.tags must contain at most 6 public index terms"]

    issues: list[str] = []
    normalized: list[str] = []
    for index, item in enumerate(tags):
        if not isinstance(item, str) or not item.strip():
            issues.append(f"play.tags[{index}] must be a non-empty string")
            continue
        tag = item.strip()
        normalized.append(tag)
        if tag != item:
            issues.append(f"play tag has surrounding whitespace: {item!r}")
        if tag.startswith("#"):
            issues.append(f"play tag must not contain a display prefix: {tag}")
        if len(tag) > 12:
            issues.append(f"play tag is too long to be an index term: {tag}")
        if tag in _FORBIDDEN_EXACT_TAGS or any(
            pattern.search(tag) for pattern in _INSTANCE_PATTERNS
        ):
            issues.append(f"play tag contains instance or workflow state: {tag}")
    if len(normalized) != len(set(normalized)):
        issues.append("play.tags contains duplicates")
    return issues


def public_screen_tag_issues(bindings: object) -> list[str]:
    """Validate screen tags as visible UI identities and stable states."""

    if not isinstance(bindings, list):
        return ["screen_tags must be a list"]

    issues: list[str] = []
    seen_screen_ids: set[str] = set()
    for binding_index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            issues.append(f"screen_tags[{binding_index}] must be an object")
            continue
        screen_id = binding.get("screen_state_id")
        if not isinstance(screen_id, str) or not screen_id.strip():
            issues.append(f"screen_tags[{binding_index}].screen_state_id must be a string")
        elif screen_id in seen_screen_ids:
            issues.append(f"screen_tags has duplicate screen_state_id: {screen_id}")
        else:
            seen_screen_ids.add(screen_id)

        tags = binding.get("tags")
        if not isinstance(tags, list):
            issues.append(f"screen_tags[{binding_index}].tags must be a list")
            continue
        if not tags:
            issues.append(f"screen_tags[{binding_index}].tags must not be empty")
        if len(tags) > 6:
            issues.append(f"screen_tags[{binding_index}].tags must contain at most 6 terms")

        normalized: list[str] = []
        for tag_index, item in enumerate(tags):
            if not isinstance(item, str) or not item.strip():
                issues.append(
                    f"screen_tags[{binding_index}].tags[{tag_index}] "
                    "must be a non-empty string"
                )
                continue
            tag = item.strip()
            normalized.append(tag)
            if tag != item:
                issues.append(f"screen tag has surrounding whitespace: {item!r}")
            if tag.startswith("#"):
                issues.append(f"screen tag must not contain a display prefix: {tag}")
            if len(tag) > 12:
                issues.append(f"screen tag is too long to be an index term: {tag}")
            if tag in _FORBIDDEN_EXACT_TAGS or any(
                pattern.search(tag) for pattern in _SCREEN_INSTANCE_OR_WORKFLOW_PATTERNS
            ):
                issues.append(f"screen tag contains instance or workflow state: {tag}")
        if len(normalized) != len(set(normalized)):
            issues.append(f"screen_tags[{binding_index}].tags contains duplicates")
    return issues


def unknown_public_play_tags(tags: Iterable[str]) -> list[str]:
    return sorted({tag for tag in tags if tag not in PLAY_TAG_DEFINITIONS})


def public_play_tag_details(tags: Iterable[str]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for tag in tags:
        definition = PLAY_TAG_DEFINITIONS.get(tag)
        details.append(
            definition.as_dict()
            if definition is not None
            else {
                "tag": tag,
                "facet": "未分类",
                "description": "该标签尚未进入公开受控词表。",
            }
        )
    return details


def public_play_tag_taxonomy() -> list[dict[str, str]]:
    return [PLAY_TAG_DEFINITIONS[tag].as_dict() for tag in sorted(PLAY_TAG_DEFINITIONS)]


def public_tag_scopes() -> list[dict[str, object]]:
    return [
        {
            **scope,
            "examples": list(scope["examples"]),
        }
        for scope in _PUBLIC_TAG_SCOPES
    ]
