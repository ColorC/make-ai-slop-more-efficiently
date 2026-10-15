"""Render the six permissive-table documents in the same three layers.

Every number on these pages comes out of the payload the extractor built from
the configuration JSON, the save-schema reconciliation or the fact stream; the
prose only names what a column means and where its truth source is.
"""

from __future__ import annotations

from typing import Any, Callable

from .render_live import (
    LAYER_CONFIG,
    LAYER_LIVE,
    LAYER_OPEN,
    _json,
    _registry_section,
    _table,
    localization_block,
)


def _ledger_section(data: dict[str, Any]) -> list[str]:
    lines = [
        "### 台账口径",
        "",
        "本文件 `配表` 里每张表在字段级台账上的状态；`observed` 是被实机事实确认过的字段数。",
        "",
    ]
    lines.extend(
        _table(
            ["表", "字段数", "observed", "contradicted", "unobserved"],
            [
                [row["table"], row["fields"], row["observed"], row["contradicted"], row["unobserved"]]
                for row in data.get("ledger") or []
            ],
            aligns="lrrrr",
        )
    )
    return lines


def _open_layer(data: dict[str, Any], notes: list[str]) -> list[str]:
    """One third-layer heading: this page's own open points, then the registry.

    ``_registry_section`` writes the heading itself, so its first two lines are
    dropped here to keep a single ``## 3`` per document.
    """

    return [LAYER_OPEN, "", *notes, *_registry_section(data)[2:]]


def _verdict(value: Any, yes: str, no: str, unknown: str = "未知") -> str:
    if value is None:
        return unknown
    return yes if value else no


# --------------------------------------------------------------------------- #
# 1. Save schema
# --------------------------------------------------------------------------- #


def render_save_schema(data: dict[str, Any]) -> list[str]:
    totals = data["totals"]
    reader = data["reader"]
    lines = [
        LAYER_CONFIG,
        "",
        "`DBTableConfigs` 是存档表结构的声明表：每个逻辑名给出实际的 SQLite 表名、列名与列类型、"
        "主键，以及写入顺序 `arrOrder`。列类型只用三种取值。",
        "",
        "### 1.1 列类型枚举",
        "",
    ]
    lines.extend(
        _table(
            ["枚举名", "SQLite 类型"],
            [[name, value] for name, value in data["value_types"].items()],
            aligns="ll",
        )
    )
    lines.extend(["### 1.2 声明表逐表", ""])
    lines.extend(
        _table(
            ["逻辑名", "存档表", "声明形态", "主键", "列数", "列（名:类型）"],
            [
                [
                    row["logical"],
                    row["save_table"],
                    row["declaration"],
                    row["primary_key"],
                    row["declared_column_count"],
                    ", ".join(
                        f"{name}:{kind}" for name, kind in row["declared_columns"].items()
                    ),
                ]
                for row in data["tables"]
            ],
            aligns="lllrrl",
        )
    )
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "对账对象是一份实机存档副本；下表口径与对账报告同源。",
            "",
            f"- 存档副本 `{data['save'].get('source_ref', '')}`",
            f"- 副本 sha256 `{data['save'].get('encrypted_sha256', '')}`",
            "",
            "### 2.1 对账总量",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "数量"],
            [
                ["声明表", totals.get("declared_tables")],
                ["实机表", totals.get("live_tables")],
                ["表名与列全一致", totals.get("match")],
                ["列集合不一致", totals.get("column_set_differs")],
                ["列类型不一致", totals.get("type_differs")],
                ["仅声明（实机无此表）", totals.get("declared_only")],
                ["仅实机（声明无此表）", totals.get("live_only")],
                ["声明列总数", totals.get("declared_columns")],
                ["同名列", totals.get("matched_columns")],
                ["仅实机列", totals.get("live_only_columns")],
                ["save_reader 已解码的声明表", reader.get("read_table_count")],
                ["save_reader 从未读过的声明表", reader.get("never_read_count")],
            ],
            aligns="lr",
        )
    )
    lines.extend(["### 2.2 与声明不一致的表", ""])
    lines.extend(
        _table(
            ["逻辑名", "存档表", "判定", "仅声明列", "仅实机列", "类型不一致"],
            [
                [
                    row["logical"],
                    row["save_table"],
                    row["verdict"],
                    row["declared_only_columns"],
                    row["live_only_columns"],
                    row["type_mismatches"],
                ]
                for row in data["differences"]
            ],
            aligns="llllll",
        )
    )
    lines.extend(
        [
            "### 2.3 save_reader 已解码的表",
            "",
            f"`{_json(reader['read_tables'])}`",
            "",
            "### 2.4 逐表实机状态",
            "",
        ]
    )
    lines.extend(
        _table(
            ["逻辑名", "存档表", "判定", "副本内行数", "save_reader 已解码"],
            [
                [
                    row["logical"],
                    row["save_table"],
                    row["verdict"],
                    row["live_row_count"],
                    row["read_by_save_reader"],
                ]
                for row in data["tables"]
            ],
            aligns="lllrl",
        )
    )
    lines.extend(_ledger_section(data))
    notes = [
        "下面按表名把 `save_reader` 从未解码的声明表分成十组，作为「下一批可直读状态」的"
        "候选清单。分组依据只有表名，不代表已知其行语义。",
        "",
        *_table(
            ["组", "表数", "表"],
            [
                [group["group"], group["table_count"], ", ".join(group["tables"])]
                for group in data["next_batch_groups"]
            ],
            aligns="lrl",
        ),
        "副本内行数为 0 的表说明这份快照当时还没写过它们，不说明该表永远为空。",
        "",
    ]
    lines.extend(_open_layer(data, notes))
    return lines


# --------------------------------------------------------------------------- #
# 2. Board prompts
# --------------------------------------------------------------------------- #


def _paperbox_section(data: dict[str, Any]) -> list[str]:
    """Sand strips as the fact stream recorded them, against the reward table."""

    paperbox = data.get("paperbox_clear") or {}
    if not paperbox.get("available"):
        return ["### 2.3 沙土格清除的实机归因", "", "事实流里还没有剥层样本。", ""]
    lines = [
        "### 2.3 沙土格清除的实机归因",
        "",
        "沙土（`pb` 前缀）不是价格而是覆盖层：清掉它按 `MainPaperboxExtraRewardConfig` 的"
        "同格取值发奖励，配置层的整张网格在 `board-initial-layout`。剥层不是被点出来的，"
        "而是合成的副作用——每条事实都记下了当次合成落点与被剥格的相对位置。",
        "",
        f"- 剥层事实数：{paperbox['event_count']}",
        f"- 其中与当次唯一已确认合成落点四邻相接：{paperbox['adjacent_count']}",
        f"- 其中在奖励表同格写有奖励：{paperbox['with_declared_reward']}",
        f"- 判据来源 `{paperbox['rule']}`；反向核对：{paperbox['reverse_direction']}",
        f"- 报告来源 `{paperbox['source']}`",
        "",
    ]
    lines.extend(
        _table(
            ["行", "列", "剥层前", "剥层后", "合成落点", "合成产物", "四邻相接", "同格声明奖励", "fact id"],
            [
                [
                    row["row"],
                    row["column"],
                    row["cell_before"],
                    row["cell_after"],
                    row["merge_target"],
                    row["merge_result_type"],
                    row["adjacent_to_merge_target"],
                    row["declared_reward"],
                    row["fact_id"],
                ]
                for row in paperbox["events"]
            ],
            aligns="rrllllll",
        )
    )
    return lines


def render_board_prompts_and_rules(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "`BoardPromptConfig` 不是文案表。它是棋盘闲置提示的优先级注册表：`Priority` 由一个"
        "闭包在每条声明处自增一次，所以**声明顺序就是优先级排名**；`Type` 是提示种类；"
        "`CloseCondition` 决定这条提示何时退出竞争。运行时按优先级升序取第一条"
        "「未关闭且可启动」的提示播放动效（合成对拉手指、点开箱子、交单等），"
        "任何强引导进行中一律不出提示。",
        "",
        f"- 声明字段：`{_json(data['declared_fields'])}`",
        f"- 其中会承载玩家可见文案的字段：`{_json(data['text_fields'])}`",
        "",
        "### 1.1 CloseCondition 编码",
        "",
        "单段数字＝玩家等级低于该值时才参与竞争；两段 `a_b` ＝主线任务 `a_b` 未完成时才参与竞争。",
        "",
        "### 1.2 提示优先级表",
        "",
    ]
    lines.extend(
        _table(
            ["优先级", "Type", "CloseCondition", "退出条件含义"],
            [
                [
                    prompt["rank"],
                    prompt["type"],
                    prompt["close_condition"]["raw"],
                    prompt["close_condition"]["meaning"],
                ]
                for prompt in data["prompts"]
            ],
            aligns="rlll",
        )
    )
    progress = data["live_progress"]
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "本账号的进度由事实流给出，据此判断每条提示此刻是否还会出现。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["已结算主线任务数", progress["fact_count"]],
                ["当前主线任务", progress["current_task"]],
                ["已达等级", progress["level"]],
                ["提示条数", data["prompt_count"]],
                ["带退出条件的提示条数", data["scoped_count"]],
            ],
            aligns="lr",
        )
    )
    lines.extend(["### 2.1 当前账号下的开闭状态", ""])
    lines.extend(
        _table(
            ["优先级", "Type", "退出条件含义", "此刻是否仍参与竞争"],
            [
                [
                    prompt["rank"],
                    prompt["type"],
                    prompt["close_condition"]["meaning"],
                    _verdict(prompt["close_condition"]["open_now"], "是", "否", "无条件/无法判定"),
                ]
                for prompt in data["prompts"]
            ],
            aligns="rlll",
        )
    )
    lines.extend(
        [
            "### 2.2 与实机已登记界面/教程链的同名对照",
            "",
            "把 `Type` 拆成词后，只用**只属于这一个 Type** 的词去和已登记的界面名、教程链名做匹配；"
            "在多个 Type 之间共用的词（命名骨架词）一律丢弃。命中表示同一玩法词出现在实机登记里，"
            "不等于语义已确认。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["Type", "该 Type 独有的词", "命中的已登记名"],
            [
                [prompt["type"], prompt["distinctive_tokens"], prompt["live_name_matches"]]
                for prompt in data["prompts"]
            ],
            aligns="lll",
        )
    )
    lines.extend(_paperbox_section(data))
    lines.extend(_ledger_section(data))
    lines.extend(localization_block(data, "### 棋盘提示表的本地化键"))
    notes = [
        "本表不含文案，所以与实机气泡文案没有逐条对照关系：实机登记到的气泡文字属于强引导"
        "（`TutorialConfig` 链）与剧情，不由本表提供。本表能解释的是「没有任何引导时，"
        "棋盘为什么会自己动起来指向某一步」。",
        "",
        "多数提示种类带活动棋盘前缀（农场、贴纸、跌落、挖掘、传送门），本账号还没进过这些玩法，"
        "所以它们此刻是否真的会触发无法从事实流判定；能判定的只有带退出条件的那几条。",
        "",
    ]
    lines.extend(_open_layer(data, notes))
    return lines


# --------------------------------------------------------------------------- #
# 3. Story speakers
# --------------------------------------------------------------------------- #


def render_story_speakers(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "`StorySpeakerConfig` 是剧情说话人的表现参数表，按角色名索引：气泡朝向、名牌与气泡底色、"
        "名字颜色，以及引导头像位置。它只描述「谁说话时长什么样」，不含台词。",
        "",
    ]
    lines.extend(
        _table(
            ["口径", "数量"],
            [
                ["角色条目", data["speaker_count"]],
                ["去掉场景/时装后缀后的基名", data["base_count"]],
                ["主线任务里出现过的出场角色名（去重）", data["task_character_count"]],
            ],
            aligns="lr",
        )
    )
    lines.extend(
        [
            f"- 气泡朝向分布：`{_json(data['direction_distribution'])}`",
            "",
            "### 1.1 逐角色",
            "",
        ]
    )
    lines.extend(
        _table(
            [
                "角色",
                "基名",
                "朝向",
                "名牌色",
                "名字色",
                "气泡色",
                "有引导头像位",
                "同名任务行数",
                "同基名任务行数",
            ],
            [
                [
                    speaker["speaker"],
                    speaker["base"],
                    speaker["direction"],
                    speaker["nameplate_colour"],
                    speaker["name_colour"],
                    speaker["bubble_colour"],
                    speaker["has_tutorial_avatar_pos"],
                    speaker["exact_task_rows"],
                    speaker["base_task_rows"],
                ]
                for speaker in data["speakers"]
            ],
            aligns="lllllllrr",
        )
    )
    lines.extend(["### 1.2 同一角色的多套表现", ""])
    lines.extend(
        _table(
            ["基名", "变体", "同基名任务行数"],
            [
                [group["base"], group["variants"], group["base_task_rows"]]
                for group in data["variant_groups"]
            ],
            aligns="llr",
        )
    )
    progress = data["live_progress"]
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "实机能确认的只有「这个角色出现在已结算的主线任务上」：主线任务的 `detail_icon` 就是"
            "该任务的出场角色，事实流记下了哪些任务已经结算。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["已结算主线任务", _json(progress["settled_tasks"])],
                ["当前主线任务", progress["current_task"]],
                ["已结算任务涉及的角色基名", _json(data["settled_characters"])],
                ["已在实机出现过的角色条目数", data["live_verified_rows"]],
            ],
            aligns="ll",
        )
    )
    lines.extend(["### 2.1 已在实机出现过的角色条目", ""])
    lines.extend(
        _table(
            ["角色", "基名", "同名任务行数", "同基名任务行数"],
            [
                [
                    speaker["speaker"],
                    speaker["base"],
                    speaker["exact_task_rows"],
                    speaker["base_task_rows"],
                ]
                for speaker in data["speakers"]
                if speaker["seen_in_settled_tasks"]
            ],
            aligns="llrr",
        )
    )
    lines.extend(_ledger_section(data))
    notes = [
        f"- 台词本体未知：{data['text_status']}。本表只能给出说话人，不能给出说了什么。",
        "- 从未在主线任务里出现的角色条目（可能只用于活动、亲密度或分支剧情）：",
        "",
        f"`{_json(data['speakers_without_tasks'])}`",
        "",
        "- 主线任务用到、但角色表里既无同名条目也无同基名条目的出场角色：",
        "",
        f"`{_json(data['task_only_characters'])}`",
        "",
    ]
    lines.extend(_open_layer(data, notes))
    return lines


# --------------------------------------------------------------------------- #
# 4. Special orders
# --------------------------------------------------------------------------- #


def _order_row_section(data: dict[str, Any]) -> list[str]:
    """The HUD order row as the replay corpus measured it, not as it was assumed."""

    rules = data.get("row_rules") or {}
    if not rules.get("available"):
        return ["### 2.1 订单行实机规则", "", "本次生成没有读到订单显示序取证，未渲染。", ""]
    corpus = rules["corpus"]
    lines = [
        "### 2.1 订单行实机规则（常规订单槽）",
        "",
        "本节讲的是 HUD 上那一行常规订单，与本表的剧情订单无关，放在这里是因为它是订单系统"
        "唯一有实机取证的部分。屏幕只给三个托盘位，存档却可以有更多槽，所以「第几个托盘」"
        "不等于「第几个槽」。",
        "",
        f"- 托盘位数：{rules['display_positions']}；寻址方式 `{rules['slot_addressing']}`；"
        f"交付交互 `{rules['delivery_interaction']}`",
        f"- 托盘中心 `{_json(rules['tray_centers'])}`（标定来源 `{rules['geometry_source']}`）",
        f"- 语料：{corpus['runs']} 个 run、{corpus['planner_rounds']} 个规划回合，"
        f"其中 {corpus['rounds_with_enough_orders']} 个回合订单数足够比对、"
        f"{corpus['rounds_with_buttons']} 个回合屏上有绿色交付按钮、"
        f"{corpus['confirmed_deliveries']} 次已确认交付",
        f"- 存档可交付数与屏上按钮数的一致性 `{_json(corpus['save_screen_agreement'])}`",
        f"- 取证来源 `{rules['source']}`",
        "",
        "候选显示序逐条打分（命中率 = 该规则预测的按钮位置与实帧一致的比例）：",
        "",
    ]
    lines.extend(
        _table(
            ["候选规则", "按钮位命中/样本", "命中率", "交付配对命中/样本", "配对命中率", "幸存"],
            [
                [
                    row["rule"],
                    f'{row["button_pattern_hits"]}/{row["button_pattern_samples"]}',
                    row["button_pattern_hit_rate"],
                    f'{row["confirmed_pairing_hits"]}/{row["confirmed_pairing_samples"]}',
                    row["confirmed_pairing_hit_rate"],
                    row["survivor"],
                ]
                for row in rules["rule_scores"]
            ],
            aligns="llrlrl",
        )
    )
    lines.extend(
        [
            "打满的规则都带同一个前缀：**可交付的订单恒在最左**。它们之间的分歧只在第 2、3 位，"
            "而本语料从未在第 2、3 位见过绿按钮，所以下面两条到今天为止无法分辨，"
            "规划器仍按「显示位 + 可见绿按钮」寻址，不赌其中任何一条。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["待分辨候选", "状态", "按钮位", "交付配对", "人工读帧", "分辨条件"],
            [
                [
                    row["rule"],
                    row["status"],
                    row["button_pattern"],
                    row["confirmed_pairing"],
                    row["hand_read"],
                    row["discriminating_check"],
                ]
                for row in rules["candidates"]
            ],
        )
    )
    fix = rules.get("type_two_fix") or {}
    if fix.get("finding"):
        lines.extend(
            [
                "被实帧推翻的一条早期规则（`type=2` 不上订单行）：",
                "",
                f"- 实测：{fix['finding']}",
                f"- 修复后的实机确认：{fix['live_confirmation']}",
                "",
            ]
        )
    occlusion = rules.get("notebook_occlusion") or {}
    if occlusion.get("available"):
        lines.extend(
            [
                "订单行还会挡住别的控件：五张订单时整行左移，首位顾客立绘盖住左上角的主线任务本。"
                "下表按等级逐帧量任务本控件颜色的命中情况，未命中的回合全部是五单回合。",
                "",
            ]
        )
        headers = occlusion["headers"]
        lines.extend(
            _table(headers, [[row.get(name) for name in headers] for row in occlusion["rows"]])
        )
    return lines


def render_order_special_types(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "`OrderSpecialConfig` 描述的是**剧情特殊订单**：由某个主线任务完成触发，绑定一个角色、"
        "一条或多条专属生成器链、一段开场与结束演出，完成后给一次性奖励。它与 HUD 上四个常规"
        "订单槽不是一回事——槽位规则在订单系统文档里，本表一行就是一次剧情事件。",
        "",
        f"- 特殊订单条数：{data['order_count']}",
        f"- 奖励币种合计：`{_json(data['reward_totals'])}`",
        "",
        "### 1.1 逐条",
        "",
    ]
    lines.extend(
        _table(
            [
                "Id",
                "角色",
                "触发",
                "需求",
                "生成器链",
                "开场奖励",
                "完成奖励",
                "边框色",
                "描述键",
            ],
            [
                [
                    order["id"],
                    order["avatar_image"],
                    order["trigger"],
                    order["requirements"],
                    order["generator_chains"],
                    order["start_reward"],
                    order["rewards"],
                    order["frame_colour"],
                    order["description_key"],
                ]
                for order in data["orders"]
            ],
            aligns="rllllllll",
        )
    )
    lines.extend(["### 1.2 演出时间线", ""])
    lines.extend(
        _table(
            ["Id", "开场时间线", "结束时间线", "大头像", "完成图", "奖励图"],
            [
                [
                    order["id"],
                    order["timelines"][0],
                    order["timelines"][1],
                    order["big_avatar_image"],
                    order["complete_image"],
                    order["reward_image"],
                ]
                for order in data["orders"]
            ],
            aligns="llllll",
        )
    )
    progress = data["live_progress"]
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "每条特殊订单的触发条件都是「某个主线任务完成」，所以本账号的主线进度直接决定它们是否"
            "可能出现过。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["当前主线任务", progress["current_task"]],
                ["已结算主线任务数", progress["fact_count"]],
                ["已触达触发点的特殊订单数", data["live_verified_rows"]],
                ["尚未触达的触发点", _json(data["unreached"])],
            ],
            aligns="ll",
        )
    )
    lines.extend(_order_row_section(data))
    lines.extend(["### 2.2 逐条触达判定", ""])
    lines.extend(
        _table(
            ["Id", "触发任务", "是否已触达"],
            [
                [
                    order["id"],
                    order["trigger_task"],
                    _verdict(order["trigger_reached"], "是", "否", "无法判定"),
                ]
                for order in data["orders"]
            ],
            aligns="lll",
        )
    )
    lines.extend(_ledger_section(data))
    notes = [
        "常规订单槽的类型语义（四个 HUD 槽、限时槽与连锁槽的附加奖励）由实机存档与订单模型反证"
        "得出，与本表无关；下表登记项保留那条反证，避免把两套「特殊」混为一谈。",
        "",
        "`Requirement*` 与 `GeneratorChain` 的编码（纯数字指向物品类型，`sp<n>_<m>` 指向专属链）"
        "只在本表内自洽，未在实机验证过，因为最早的触发点还没触达。",
        "",
    ]
    if data["warnings"]:
        notes.extend(["解析警告：", "", f"`{_json(data['warnings'])}`", ""])
    lines.extend(_open_layer(data, notes))
    return lines


# --------------------------------------------------------------------------- #
# 5. Energy economy
# --------------------------------------------------------------------------- #


def _coins_per_energy_section(data: dict[str, Any]) -> list[str]:
    """What a point of energy is worth, measured over the report corpus."""

    windows = (data.get("coins_per_energy") or {}).get("windows") or []
    lines = [
        "### 2.2 每点体力换到的金币",
        "",
        "体力只买生成，合成与交付都不花体力，所以「一点体力换多少金币」就是这个账号的推进"
        "速率。带报告来源的那几行是全语料口径：确认的交付金币除以确认花掉的体力，语料每长"
        "一截就重算一次，行按报告时间从新到旧；最后一行换一种口径，是升级之后那一段的存档"
        "净差分（体力从满到零、金币的净增）。口径不同，比值落在同一量级。",
        "",
    ]
    lines.extend(
        _table(
            ["窗口", "报告/段数", "体力净减", "金币净增", "交付次数", "金币/体力", "来源"],
            [
                [
                    row["window"],
                    row["reports"],
                    row["energy_spent"],
                    row["delivery_coins"],
                    row["deliveries"],
                    row["coins_per_energy"],
                    row["source"],
                ]
                for row in windows
            ],
            aligns="lrrrrrl",
        )
    )
    return lines


def _wait_granularity_section(data: dict[str, Any]) -> list[str]:
    """The measured A/B on the resume threshold, plus the default it derives."""

    granularity = data.get("wait_granularity") or {}
    if not granularity.get("available"):
        return ["### 2.3 等待粒度 A/B", "", "本次生成没有读到 A/B 报告，未渲染。", ""]
    inputs = granularity.get("derivation_inputs") or {}
    lines = [
        "### 2.3 等待粒度 A/B 与默认阈值",
        "",
        "体力回复限速之后，唯一还能调的是「攒到几点体力再醒」。两臂同账号同稳态各跑一段"
        "相同墙钟：A 臂阈值为 1（醒得最勤），B 臂用推导出的默认值。",
        "",
        f"- 默认阈值公式：`{granularity['default_formula']}`",
        f"- 今日取值：{granularity['default_threshold']}（输入 `{_json(inputs)}`）",
        f"- 唤醒规则：{granularity['wake_rule']}",
        f"- 阈值 1 复现上一档行为：{_verdict(granularity['threshold_one_reproduces_f4_07'], '是', '否')}",
        f"- 判定：{granularity['winner']}",
        f"- 衡量口径：{granularity['why_confirmed_is_the_wrong_yardstick']}",
        f"- 每臂体力收入：{granularity['energy_income_each_arm']}",
        f"- 保留：{granularity['caveat']}",
        f"- energy_bound 判据：{granularity['energy_bound_criterion']}",
        f"- 来源 `{granularity['source']}` 与 `{granularity['report_source']}`",
        "",
        "两臂逐项：",
        "",
    ]
    lines.extend(
        _table(
            [
                "臂",
                "阈值",
                "墙钟（秒）",
                "段数",
                "回合",
                "花掉体力",
                "等待（秒）",
                "睡眠占比",
                "有效游玩占比",
                "replay MiB",
                "交付",
                "energy_bound 起始段",
            ],
            [
                [
                    row["arm"],
                    row["threshold"]
                    if row["threshold"] is not None
                    else f"默认（当日推导为 {granularity['default_threshold']}）",
                    row["wall_seconds"],
                    row["segments"],
                    row["rounds"],
                    row["energy_spent"],
                    row["waited_s"],
                    row["sleep_share"],
                    row["effective_play_share"],
                    row["replay_mib"],
                    row["confirmed_deliveries"],
                    row["energy_bound_entered_at_segment"],
                ]
                for row in granularity["arms"]
            ],
            aligns="llrrrrrrrrrr",
        )
    )
    lines.extend(["逐指标对照（A/B 与比值）：", ""])
    lines.extend(
        _table(
            ["指标", "A", "B", "B/A"],
            [
                [row.get("metric"), row.get("A"), row.get("B"), row.get("ratio_second_over_first")]
                for row in granularity.get("table") or []
            ],
            aligns="lrrr",
        )
    )
    derived = granularity.get("derived") or {}
    if derived:
        lines.extend(["按体力折算的派生量：", ""])
        lines.extend(
            _table(
                ["派生量", "A", "B", "B/A"],
                [
                    [name, value.get("A"), value.get("B"), value.get("b_over_a")]
                    for name, value in sorted(derived.items())
                    if isinstance(value, dict)
                ],
                aligns="lrrr",
            )
        )
    return lines


def _free_grant_section(data: dict[str, Any]) -> list[str]:
    """Free grants of energy, each carrying the save read either side of it."""

    lines = [
        "### 2.4 免费发放的存档差分",
        "",
        "无条件发放的体力都有存档前后读数；没有差分的那一行说明该点击本身不发东西，"
        "内容早已入账。",
        "",
    ]
    lines.extend(
        _table(
            ["发放", "体力前", "体力后", "宝石前", "宝石后", "证据", "来源"],
            [
                [
                    row["grant"],
                    row["energy_before"],
                    row["energy_after"],
                    row["gems_before"],
                    row["gems_after"],
                    row["evidence"],
                    row["source"],
                ]
                for row in data.get("free_grants") or []
            ],
            aligns="lrrrrll",
        )
    )
    return lines


def render_energy_items(data: dict[str, Any]) -> list[str]:
    constants = data["constants"]
    chain = data["energy_chain"] or {}
    lines = [
        LAYER_CONFIG,
        "",
        "体力经济由三处声明拼起来：体力上限与恢复周期是反编译模型里的常量；棋盘上的体力补给物是"
        "物品表里带 `CollectRewards` 的一条链；`EnergyBoostItemConfig` 则是可以开启体力加速的"
        "生成器白名单——它不是体力物品表，而是允许一次点击花 2 的 n 次方体力换同样倍数产出的"
        "物品类型清单。",
        "",
        f"### 1.1 模型常量（来源 `{data['constants_source']}`）",
        "",
    ]
    lines.extend(
        _table(
            ["常量", "值"],
            [[name, value] for name, value in sorted(constants.items())],
            aligns="lr",
        )
    )
    lines.extend(["### 1.2 体力补给链", ""])
    lines.extend(
        _table(
            ["Type", "MergedType", "领取得到的体力"],
            [
                [level["type"], level["merged_type"], level["amount"]]
                for level in chain.get("levels") or []
            ],
            aligns="llr",
        )
    )
    lines.extend(
        [
            "### 1.3 其它同形态的收集链（对照）",
            "",
            "`CollectRewards` 不止体力一种；同一套「选中再点一次即领取」的交互覆盖下列币种。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["币种", "层数", "Type 序列", "数值序列"],
            [
                [item["currency"], item["level_count"], item["sequence"], item["amounts"]]
                for item in data["collect_chains"]
            ],
            aligns="lrll",
        )
    )
    lines.extend(
        [
            "### 1.3.1 合成优于分领（由链数值推出）",
            "",
            "同一条链上，把两颗低级补给合成再领，与直接分别领两颗，二者的收益差就写在 "
            "`CollectRewards` 里：下表每一行的右侧恒为正，所以只要还能合就不该分领。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["低级 Type", "合成后 Type", "分领两颗", "合成后领一次", "差额", "合成更优"],
            [
                [
                    row["from_type"],
                    row["to_type"],
                    row["collect_two"],
                    row["merge_then_collect"],
                    row["difference"],
                    row["merge_wins"],
                ]
                for row in data.get("merge_beats_collect") or []
            ],
            aligns="llrrrl",
        )
    )
    lines.extend(
        [
            "### 1.4 体力加速白名单",
            "",
            f"- 条目数：{data['whitelist_count']}",
            f"- 按链分布：`{_json(data['whitelist_chains'])}`",
            f"- 未解析成字面值的引用：`{_json(data['whitelist_unresolved'])}`",
            f"- 在物品表里查不到的类型：`{_json(data['whitelist_missing_from_item_model'])}`",
            "",
        ]
    )
    lines.extend(
        _table(
            ["引用", "物品类型", "所属链", "在物品表内", "是生成器", "单次产出耗体力"],
            [
                [
                    entry["ref"],
                    entry["item_type"],
                    entry["chain"],
                    entry["in_item_model"],
                    entry["is_generator"],
                    entry["spread_cost_energy"],
                ]
                for entry in data["whitelist"]
            ],
            aligns="lllllr",
        )
    )
    summary = data["recovery_summary"]
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "恢复时钟由存档时间序列验证：两次存档之间没有消耗动作时，体力增量应当等于间隔除以"
            "恢复周期取整。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["空闲区间事实数", summary["fact_count"]],
                ["预测完全命中", summary["exact"]],
                ["偏差不超过 1 点", summary["within_one"]],
                ["事实流里出现过的体力最大值", summary["observed_energy_max"]],
                ["事实流里出现过的体力最小值", summary["observed_energy_min"]],
            ],
            aligns="lr",
        )
    )
    check = data.get("recovery_live_check") or {}
    if check:
        lines.extend(
            [
                "跨越一次完整停机的单点核对（体力归零到再次读数）：",
                "",
            ]
        )
        lines.extend(
            _table(
                ["口径", "值"],
                [
                    ["归零时刻", check.get("energy_zero_at")],
                    ["再次读数时刻", check.get("observed_energy_at")],
                    ["间隔（秒）", check.get("elapsed_s")],
                    ["读到的体力", check.get("observed_energy")],
                    ["按恢复周期预测", check.get("predicted")],
                    ["来源", data.get("recovery_live_source")],
                ],
                aligns="ll",
            )
        )
    lines.extend(["### 2.1 逐段空闲恢复", ""])
    lines.extend(
        _table(
            ["事实 id", "间隔（秒）", "体力前", "体力后", "实测增量", "按周期预测", "偏差"],
            [
                [
                    row["fact_id"],
                    None if row["elapsed_seconds"] is None else round(row["elapsed_seconds"]),
                    row["energy_before"],
                    row["energy_after"],
                    row["energy_gained"],
                    row["predicted_gain"],
                    row["deviation"],
                ]
                for row in data["recovery_facts"]
            ],
            aligns="lrrrrrr",
        )
    )
    lines.extend(_coins_per_energy_section(data))
    lines.extend(_wait_granularity_section(data))
    lines.extend(_free_grant_section(data))
    lines.extend(_ledger_section(data))
    notes = [
        "- 体力补给链的领取数值只有声明层：事实流里还没有 `collect` 动作样本，"
        "所以 `CollectRewards` 这一列在台账上仍是未观测。",
        "- 事实流里出现过的体力峰值高于声明上限（见上表），说明补给发放不按上限截断；"
        "上限只约束自然恢复的终点。",
        "- 白名单是否在本账号可用还取决于服务端开关（体力加速属服务端下发的通用配置），"
        "本地表只说明「哪些物品被允许加速」。",
        "",
    ]
    lines.extend(_open_layer(data, notes))
    return lines


# --------------------------------------------------------------------------- #
# 6. Skins and item views
# --------------------------------------------------------------------------- #


def render_skins_and_item_views(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "这两张表是资源侧的大表，只做规模与结构摘要，不逐行展开：`SkinConfig` 把每件装饰品挂到"
        "一个装饰区、一个槽位和一段演出；`ItemViewConfig` 给每个物品类型指定贴图，少数条目另有"
        "详情描述、教学棋盘或挖掘/传送门专用外观。",
        "",
        "### 1.1 规模",
        "",
    ]
    lines.extend(
        _table(
            ["口径", "数量"],
            [[name, value] for name, value in data["scale"].items()],
            aligns="lr",
        )
    )
    lines.extend(["### 1.2 SkinConfig 字段字典", ""])
    lines.extend(
        _table(
            ["字段", "出现行数", "取值形态"],
            [[row["field"], row["rows"], row["shapes"]] for row in data["skin_fields"]],
            aligns="lrl",
        )
    )
    lines.extend(["### 1.3 SkinConfig 按装饰区计数", ""])
    lines.extend(
        _table(
            ["装饰区", "行数"],
            [[row["area"], row["rows"]] for row in data["skin_areas"]],
            aligns="lr",
        )
    )
    lines.extend(["### 1.4 SkinConfig 按槽位前缀计数", ""])
    lines.extend(
        _table(
            ["槽位前缀", "行数"],
            [[row["prefix"], row["rows"]] for row in data["skin_slot_prefixes"]],
            aligns="lr",
        )
    )
    lines.extend(["### 1.5 ItemViewConfig 字段字典", ""])
    lines.extend(
        _table(
            ["字段", "出现行数", "取值形态"],
            [[row["field"], row["rows"], row["shapes"]] for row in data["view_fields"]],
            aligns="lrl",
        )
    )
    lines.extend(
        [
            "### 1.6 ItemViewConfig 结构分布",
            "",
            f"- Type 形态：`{_json(data['view_type_kinds'])}`",
            f"- 每行贴图数分布：`{_json(data['view_image_counts'])}`",
            "",
            "按挖掘外观预制体计数：",
            "",
        ]
    )
    lines.extend(
        _table(
            ["挖掘外观预制体", "行数"],
            [[row["prefab"], row["rows"]] for row in data["view_dig_views"]],
            aligns="lr",
        )
    )
    lines.extend(["带教学棋盘标记的条目：", ""])
    lines.extend(
        _table(
            ["Type", "TutorialBoard"],
            [[row["type"], row["tutorial_board"]] for row in data["view_tutorial_boards"]],
            aligns="ll",
        )
    )
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "这两张表描述的是外观资源，本账号的长跑没有产生能确认其中任何一行的事实；"
            "下表就是它们当前的台账状态。",
            "",
        ]
    )
    lines.extend(_ledger_section(data))
    notes = [
        "- `SkinConfig` 的 `area` 是可寻址资源标签，`timeline` 是演出片段名；两者都指向包外"
        "资源，反编译材料里没有它们的内容。",
        "- 装饰以装饰区为单位成批投放，本表不含时间字段，投放窗口属于运营活动。",
        "- `ItemViewConfig` 只把物品类型映射到贴图；贴图本体同样在包外，本文档不做资源核对。",
        "",
    ]
    lines.extend(_open_layer(data, notes))
    return lines


PERMISSIVE_RENDERERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "save-schema": render_save_schema,
    "board-prompts-and-rules": render_board_prompts_and_rules,
    "story-speakers": render_story_speakers,
    "order-special-types": render_order_special_types,
    "energy-items": render_energy_items,
    "skins-and-item-views": render_skins_and_item_views,
}
