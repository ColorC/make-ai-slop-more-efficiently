# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=format status=active
# [OMNI] material_id="material:services.learning.gddecon.formats.aspect_tree_contracts.py"
"""gddecon Materials (Format) —— 游戏设计拆解的数据契约。

三块:
  gddecon.deconstruction-request  (source) — 拆解请求: 给哪款游戏、设计源在哪、build 在哪。
  gddecon.aspect-tree             (sink)   — 方面树: 设计应被拆成的维度树 + 每个方面的发现证据。
  gddecon.discovery-method        (sink)   — 当次实际用到的发现法快照 (透镜册 + 展开规则 + 完备性)。

遵循 material.md: 每块带 kind.* tag; description 写成「语言锚」(无背景 agent 据此能复现)。
不打分 (拒绝压缩维度数字), 只列证据。
"""
from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


# ── 入口 (source) ──────────────────────────────────────────────────────────
GDDECON_REQUEST = Format(
    id="gddecon.deconstruction-request",
    name="GameDesignDeconstructionRequest",
    description=(
        "拆解一款游戏设计为「方面树」的请求。字段: "
        "game_name(游戏名); design_sources(设计文档/目录路径列表, 一手设计源: GDD/设定/规格/废案); "
        "build_root(当前 build 根目录, 供读现态证据如 DOM 快照/截图/代码); "
        "build_evidence(可选, 已知的现态观察或失败现象列表, 字符串); "
        "focus(可选, 只下钻某个子领域, 空=整树); "
        "project_root(只读工具寻址根, 默认进程 cwd)。"
        "上游承诺: design_sources 与 build_root 路径存在可读。"
        "下游用途: 拆解 agent 读这些源, 应用发现法产出方面树。"
        "最小样例: {\"game_name\":\"行者无乡\","
        "\"design_sources\":[\"C:/workspace/故事/walker-universe\"],"
        "\"build_root\":\"C:/workspace/webworks/apps/walker-game\","
        "\"focus\":\"\"}"
    ),
    tags=["domain.gddecon", "kind.source", "stage.request"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "design_sources": {"type": "array", "items": {"type": "string"}},
            "build_root": {"type": "string"},
            "build_evidence": {"type": "array", "items": {"type": "string"}},
            "focus": {"type": "string"},
            "project_root": {"type": "string"},
        },
        "required": ["game_name"],
    },
    examples=[
        {
            "game_name": "行者无乡",
            "design_sources": ["C:/workspace/故事/walker-universe"],
            "build_root": "C:/workspace/webworks/apps/walker-game",
            "focus": "",
        }
    ],
)


# ── 产出: 方面树 (sink) ─────────────────────────────────────────────────────
GDDECON_ASPECT_TREE = Format(
    id="gddecon.aspect-tree",
    name="GameDesignAspectTree",
    description=(
        "一款游戏的设计应被拆成的「方面」(可评估、可决策的维度) 的递归树。"
        "字段: game_name; aspect_count(方面总数); "
        "lenses_applied(本次用到的发现透镜名列表); "
        "aspects(方面节点数组, 每个含: id(点分层级如 ui.interaction.guidance); "
        "name; parent(父 id 或 null 表顶层); definition(一句话: 这条维度关切什么); "
        "lens(由哪个透镜发现); rationale(为何它是一条独立维度); "
        "evidence(逐条 verbatim 出处指针: 文件路径+段落/build 现象, 支撑该方面成立); "
        "live_concern(布尔, 设计应然↔当前实然在此背离=true)); "
        "completeness_notes(完备性自评: 哪些透镜跑过、连续几轮挖不出新方面才收敛、还存疑哪)。"
        "回答的是: 这款游戏大概分多少方面、如何嵌套(如 UI > 交互引导性/交互存在性/信息表达)、"
        "每个怎么发现的。不打分、不给可信度数字, 只列证据 (拒绝压缩维度数字)。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.aspect-tree"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "aspect_count": {"type": "integer"},
            "lenses_applied": {"type": "array", "items": {"type": "string"}},
            "aspects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "parent": {"type": ["string", "null"]},
                        "definition": {"type": "string"},
                        "lens": {"type": "string"},
                        "rationale": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "live_concern": {"type": "boolean"},
                    },
                    "required": ["id", "name", "parent", "definition", "rationale", "evidence"],
                },
            },
            "completeness_notes": {"type": "string"},
        },
        "required": ["game_name", "aspects"],
    },
)


# ── 产出: 当次发现法快照 (sink) ─────────────────────────────────────────────
GDDECON_DISCOVERY_METHOD = Format(
    id="gddecon.discovery-method",
    name="GameDesignDiscoveryMethod",
    description=(
        "本次拆解实际使用的「方面发现法」快照, 让方法可复用、可生长、可审计。字段: "
        "lenses(透镜册, 每条: name + 它专门发现什么方面); "
        "expansion_rules(展开规则: 下钻 / 新子系统再扫 / 背离触发 / 他山之石); "
        "stop_condition(粒度停止条件 + 完备性收敛条件); "
        "scale_rule(游戏变复杂时如何进一步发现新方面)。"
        "这是发现法本体的当次实例 —— 权威定义在 discovery_method.md, 此 material 记录该次跑的取值。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.method"],
    json_schema={
        "type": "object",
        "properties": {
            "lenses": {"type": "array", "items": {"type": "object"}},
            "expansion_rules": {"type": "array", "items": {"type": "string"}},
            "stop_condition": {"type": "string"},
            "scale_rule": {"type": "string"},
        },
    },
)


# ── 产出: 差距报告 (sink) ───────────────────────────────────────────────────
GDDECON_GAP_REPORT = Format(
    id="gddecon.gap-report",
    name="GameDesignGapReport",
    description=(
        "对一款游戏方面树里每个方面做的「应然 ↔ 实然 ↔ 差距」整体分析。字段: "
        "game_name; gaps(差距节点数组, 每个含: id(对应方面树的方面 id); name; "
        "intended(应然: 设计意图要求做到什么, 引设计源原话); "
        "actual(实然: 当前 build 实际什么样, 引 build 现象/代码/DOM, 查不到则写'现态未知'); "
        "gap(差距: 应然与实然具体差在哪——缺失/偏离/缩水/未实装, 可据此排优先级); "
        "severity(严重度分类词, 非数字: critical/major/minor/aligned); "
        "live_concern(布尔); evidence(应然+实然各≥1条 verbatim 出处)))。"
        "用途: 取代散点修复——按方面归位差距, 据 severity 与 gap 排修复优先级, "
        "是决策树「挂尺子」前的全局差距盘点。不打分只分类+列证据。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.gap-report"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "intended": {"type": "string"},
                        "actual": {"type": "string"},
                        "gap": {"type": "string"},
                        "severity": {"type": "string"},
                        "live_concern": {"type": "boolean"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "name", "intended", "actual", "gap", "severity"],
                },
            },
        },
        "required": ["game_name", "gaps"],
    },
)


# ── UI 设计生命周期：标准库 (sink) ──────────────────────────────────────────
GDDECON_UI_STANDARD = Format(
    id="gddecon.ui-standard",
    name="GameUiStandard",
    description=(
        "一款游戏的 UI 标准库——'好 UI 该满足什么'的可检查规则集，是 评估/建立/调整 UI 设计的依据。"
        "字段: game_name; scope(适用范围, 如'战斗屏'); rules(规则数组, 每条含: "
        "id(点分, 如 ui.info.focus_three); dimension(信息 / 交互, 二选一——这是分类轴); "
        "name; rule(一句可检查的硬要求, 说清'必须做到什么'); "
        "check(怎么验一份设计是否满足这条, 具体到能照着查 DOM/截图/交互); "
        "necessity(must / should, 词不打分); evidence(逐条 verbatim 出处: 规格文件 + 段落要点)))。"
        "用途: ui-evaluate 拿它逐条对标现态或提案; ui-build 拿它当设计目标; ui-revise 拿它当约束。"
        "标准随设计规格更新可重跑刷新（跟进UI标准）。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.ui-standard"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "scope": {"type": "string"},
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "dimension": {"type": "string"},
                        "name": {"type": "string"},
                        "rule": {"type": "string"},
                        "check": {"type": "string"},
                        "necessity": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "dimension", "name", "rule", "check", "necessity"],
                },
            },
        },
        "required": ["game_name", "rules"],
    },
)


# ── UI 设计生命周期：建立UI设计稿 (sink) ───────────────────────────────────
GDDECON_UI_DESIGN = Format(
    id="gddecon.ui-design",
    name="GameUiDesignDraft",
    description=(
        "按真实后端逻辑产出的一版界面设计稿（complete-expression：先求把后端所有状态+所有操作"
        "完整暴露，暂不管信息过载/不美化）。字段: game_name; scope(如'战斗屏'); "
        "body_html(用约定 class 词表写的设计稿主体 HTML，由编排器套皮肤+缩放查看器); "
        "exposes(列出本稿暴露了哪些后端状态与操作); incomplete(标注后端尚未实现/为 no-op 的点，如攻/蓄段)。"
        "grounding=真代码(game-state/game-command/battle-*)，非设计文档。"
        "用途: 让人一眼看全后端在干什么、所有操作可达；是'后端先行→前端完整体现'阶段的产物，"
        "后续再进 UI 精调(ui-standard)。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.ui-design"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "scope": {"type": "string"},
            "body_html": {"type": "string"},
            "exposes": {"type": "array", "items": {"type": "string"}},
            "incomplete": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["game_name", "body_html"],
    },
)


# ── UI 设计生命周期：信息层级（界面信息维度的核心产物）(sink) ──────────────
GDDECON_INFO_HIERARCHY = Format(
    id="gddecon.info-hierarchy",
    name="GameUiInfoHierarchy",
    description=(
        "一屏要表达的全部信息按「玩家注意力 / 行为频次」排出的层级表 —— 界面信息维度的核心决策产物。"
        "回答：哪些信息一眼必看(常驻)、哪些次级常驻、哪些按需才揭示；以及——很多信息不可能常驻同屏，"
        "「展开/揭示信息」本身就是一种操作，必须连同触发它的操作一起记录。"
        "字段: game_name; scope(如'战斗屏'); "
        "tiers(每条信息一行, 含: info(信息项, 用表达清单里的名字); "
        "tier(分层: T0一眼必看 / T1常驻次级 / T2按需揭示 / T3调试态, 词不打分); "
        "residency(常驻 / 揭示); drivers(哪些玩家时刻驱动它高或低, 行为频次依据); "
        "rationale(为何这一层); reveal_op(若揭示: 由哪个揭示操作展开露出, 常驻留空)); "
        "reveal_ops(揭示操作清单 —— 把'展开信息'当操作记录, 每条含: name; reveals(展开后露出哪些信息); "
        "kind(已有命令带出 / 纯UI揭示新增操作); trigger(怎么触发: 点角色/点卡/点日志…))。"
        "用途: 是把'完整体现'那版很密的设计稿按界面信息维度收拾的依据; 也把操作清单从 16 条扩成'操作+揭示'全集。"
        "grounding=完整表达清单(真后端) + 游戏核心循环。不打分, 只排层级 + 列驱动证据。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.info-hierarchy"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "scope": {"type": "string"},
            "tiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "info": {"type": "string"},
                        "tier": {"type": "string"},
                        "residency": {"type": "string"},
                        "drivers": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                        "reveal_op": {"type": "string"},
                    },
                    "required": ["info", "tier", "residency", "rationale"],
                },
            },
            "reveal_ops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "reveals": {"type": "array", "items": {"type": "string"}},
                        "kind": {"type": "string"},
                        "trigger": {"type": "string"},
                    },
                    "required": ["name", "reveals", "kind"],
                },
            },
        },
        "required": ["game_name", "tiers"],
    },
)


# ── UI 设计生命周期：操作交互模型（界面操作维度的核心产物）(sink) ──────────
GDDECON_INTERACTION_MODEL = Format(
    id="gddecon.interaction-model",
    name="GameUiInteractionModel",
    description=(
        "一屏全部操作（指令 + 揭示）按交互轴排出的交互模型 —— 界面操作维度的核心决策产物，"
        "是信息层级(界面信息维度)的对偶：信息按注意力分层，操作按交互需求规范。"
        "字段: game_name; scope(如'战斗屏'); "
        "operations(每个操作一行, 含: op(操作名); group(布阵/重整/时间控制/揭示); "
        "frequency(高/中/低, 行为频次); gesture(本能手势: 单击/拖拽/长按/悬停/键位/菜单); "
        "directness(直接主屏一步 / 次级多一步 / 深处菜单——高频要直接); "
        "feedback(执行后玩家从哪看到生效, 对治'看不出发生了什么'); "
        "safety(无门槛 / 显代价 / 需确认 / 可撤销——破坏性或花命令点的要拦); "
        "availability(可用相位 setup/paused/running/任意); "
        "selection_model(直接对目标 / 先选对象再操作); rationale); "
        "principles(从全表归纳的贯穿性交互原则)。"
        "用途: 是把'完整体现'那版设计稿按界面操作维度收拾的依据——决定每个操作摆哪、用什么手势、给什么反馈。"
        "grounding=操作全集(16 指令 + 揭示操作) + 游戏核心循环。不打分, 逐操作列规范 + 归纳原则。"
    ),
    tags=["domain.gddecon", "kind.sink", "stage.interaction-model"],
    json_schema={
        "type": "object",
        "properties": {
            "game_name": {"type": "string"},
            "scope": {"type": "string"},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string"},
                        "group": {"type": "string"},
                        "frequency": {"type": "string"},
                        "gesture": {"type": "string"},
                        "directness": {"type": "string"},
                        "feedback": {"type": "string"},
                        "safety": {"type": "string"},
                        "availability": {"type": "string"},
                        "selection_model": {"type": "string"},
                        "backend": {"type": "string", "description": "该操作的后端落地状态: 现有指令 / 纯UI / 建议新增(后端尚无)——防止把建议操作当现有体现"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["op", "group", "frequency", "gesture", "directness",
                                 "feedback", "safety", "availability", "selection_model"],
                },
            },
            "principles": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["game_name", "operations"],
    },
)


ALL_FORMATS = [
    GDDECON_REQUEST, GDDECON_ASPECT_TREE, GDDECON_DISCOVERY_METHOD,
    GDDECON_GAP_REPORT, GDDECON_UI_STANDARD, GDDECON_UI_DESIGN,
    GDDECON_INFO_HIERARCHY, GDDECON_INTERACTION_MODEL,
]


def register_formats(registry: FormatRegistry) -> None:
    """注册 gddecon 全部 Format (幂等)。dispatch 按约定名 getattr 自动发现。"""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
