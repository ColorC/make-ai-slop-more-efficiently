"""Render the verified layer as Markdown sections.

Every document keeps the same three headings so a reader always knows which
layer a line belongs to: what the decompiled configuration states, what the
device actually showed, and what is still guessed and therefore carries an
open-observation number.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .live_facts import mermaid_id
from .localization import MISSING


LAYER_CONFIG = "## 1 配置声明（反编译）"
LAYER_LIVE = "## 2 实机验证"
LAYER_OPEN = "## 3 推断与未知"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, dict)):
        text = _json(value) if value else ""
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]], aligns: str = "") -> list[str]:
    if not rows:
        return ["（无记录）", ""]
    alignment = (list(aligns) + ["-"] * len(headers))[: len(headers)]
    separator = "|".join(
        ":---" if mark == "l" else "---:" if mark == "r" else "---" for mark in alignment
    )
    lines = ["| " + " | ".join(headers) + " |", "|" + separator + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    lines.append("")
    return lines


def localization_block(data: dict[str, Any], heading: str) -> list[str]:
    """Render one table's key-column scan against the unpacked localization text."""

    scan = data.get("localization") or {}
    lines = [
        heading,
        "",
        "本节由随包 `Assets/Text/GameText/game_text_cn.json` 逐列校验：一列只有在存在命中且"
        "没有任何单元格是现成中文时，才被判为本地化键列并换出正文。",
        "",
    ]
    if not scan.get("available"):
        lines.extend(["本次生成没有解包的本地化文本，未做换出。", ""])
        return lines
    lines.extend(
        _table(
            ["列", "有值行数", "命中文本表", "已是中文", "判为键列"],
            [
                [
                    column["field"],
                    column["values"],
                    column["resolved"],
                    column["authored_text"],
                    column["substitutable"],
                ]
                for column in scan.get("columns") or []
            ],
            aligns="lrrrl",
        )
    )
    lines.extend(
        _table(
            ["列", "文本键", "正文"],
            [[row["field"], row["key"], row["text"]] for row in scan.get("substituted") or []],
        )
    )
    return lines


def _registry_section(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_OPEN,
        "",
        "下表来自未登记观测登记表，编号按登记表中的行序生成，归属由登记表自己的"
        "「猜测归属」列路由，不由本文档改判。",
        "",
    ]
    lines.extend(
        _table(
            ["编号", "登记日期", "观测描述", "猜测归属", "处置"],
            [
                [
                    entry.get("编号"),
                    entry.get("登记日期"),
                    entry.get("观测描述"),
                    entry.get("猜测归属"),
                    entry.get("处置"),
                ]
                for entry in data.get("registry") or []
            ],
        )
    )
    return lines


def _no_config_layer(reason: str) -> list[str]:
    return [LAYER_CONFIG, "", reason, ""]


# The condition grammar as TutorialStartCondition:_Init implements it; the value
# sets under it are counted from the table, not written by hand.
_CONDITION_GRAMMAR = (
    "`condition` 先按逗号拆成若干子句，全部满足才触发；每个子句再按第一个连字符拆成"
    "条件类型与参数串，参数串按 `#` 拆成参数表。空 `condition` 表示没有前置条件。"
)


# --------------------------------------------------------------------------- #


def render_ui_flow_network(data: dict[str, Any]) -> list[str]:
    summary = data["summary"]
    lines = _no_config_layer(
        "本系统在反编译配置里没有对应表：界面节点与转移全部来自实机勘测登记，"
        "因此本文档没有配置声明层。"
    )
    lines.extend([LAYER_LIVE, "", "### 2.1 规模", ""])
    lines.extend(
        _table(
            ["口径", "数量"],
            [
                ["界面节点", summary["surfaces"]],
                ["转移记录", summary["transitions"]],
                ["其中已验证", summary["verified_transitions"]],
                ["其中被证否", summary["refuted_transitions"]],
                ["其中两端都落在已登记界面（可入图）", summary["drawable_transitions"]],
                ["红线界面", summary["forbidden_surfaces"]],
            ],
            aligns="lr",
        )
    )
    lines.extend(
        [
            "坐标空间与语言：",
            "",
            f"- 坐标空间 `{_json(data['coordinate_space'])}`",
            f"- 界面语言 `{data['locale']}`",
            "",
            "### 2.2 界面节点",
            "",
        ]
    )
    lines.extend(
        _table(
            ["界面", "类别", "登记状态", "签名锚点数", "元素数", "证据帧数", "红线"],
            [
                [
                    node["surface"],
                    node["kind"],
                    node["status"],
                    node["anchor_count"],
                    node["element_count"],
                    len(node["evidence_frames"]),
                    node["forbidden"],
                ]
                for node in data["nodes"]
            ],
            aligns="lllrrrl",
        )
    )
    lines.extend(
        [
            "### 2.3 本阶段新登记与复核的界面",
            "",
            "入选条件由勘测条目自己决定：条目里引用了本阶段的取证报告编号，或者它带有免费领取"
            "控件（那是 free-claim 口径必须逐个处置的界面）。「允许动作」是勘测登记里没有被标成"
            "禁点、不可用的控件；其余控件在「保留动作」列，运行时一次都不点。",
            "",
        ]
    )
    lines.extend(
        _table(
            [
                "界面",
                "类别",
                "登记状态",
                "引用报告",
                "红线",
                "允许动作",
                "保留动作",
                "免费领取控件",
                "签名锚点数",
                "证据帧数",
            ],
            [
                [
                    row["surface"],
                    row["kind"],
                    row["status"],
                    row["cited_reports"],
                    row["forbidden"],
                    row["allowed_actions"],
                    row["withheld_actions"],
                    row["free_claim_controls"],
                    row["anchor_count"],
                    row["evidence_frames"],
                ]
                for row in data.get("recent_registrations") or []
            ],
            aligns="lllllllrrr",
        )
    )
    declared = [
        row for row in data.get("recent_registrations") or [] if row["declared_allowed_actions"]
    ]
    if declared:
        lines.extend(["这些界面在勘测里另有一句自述的动作口径：", ""])
        lines.extend(
            _table(
                ["界面", "自述的允许动作"],
                [[row["surface"], row["declared_allowed_actions"]] for row in declared],
            )
        )
    lines.extend(["### 2.4 转移", ""])
    lines.extend(
        _table(
            ["起点", "动作", "坐标", "预期", "实际", "判定"],
            [
                [
                    row["from"],
                    row["action"],
                    row["center"],
                    row["expected"],
                    row["actual"],
                    row["status"],
                ]
                for row in data["transitions"]
            ],
        )
    )
    lines.extend(
        [
            "### 2.5 状态图",
            "",
            "只画两端都能落到已登记界面上的转移；终点写成描述性文字的那几条留在上表里。",
            "",
            "```mermaid",
            "flowchart TD",
        ]
    )
    for node in data["nodes"]:
        shape = f'{mermaid_id(node["surface"])}["{node["surface"]}"]'
        lines.append(f"    {shape}")
    drawn: set[tuple[str, str, str]] = set()
    for row in data["transitions"]:
        source = row["resolved_source"]
        target = row["resolved_target"]
        if not source or not target:
            continue
        key = (source, target, row["status"])
        if key in drawn:
            continue
        drawn.add(key)
        arrow = "-.->" if row["status"].startswith("refuted") else "-->"
        lines.append(
            f'    {mermaid_id(source)} {arrow}|"{row["status"]}"| {mermaid_id(target)}'
        )
    for node in data["nodes"]:
        if node["forbidden"]:
            lines.append(
                f'    style {mermaid_id(node["surface"])} stroke-dasharray: 4 3'
            )
    lines.extend(["```", "", "### 2.6 红线界面与唯一允许的关闭动作", ""])
    lines.extend(
        _table(
            ["界面", "红线原因", "允许的关闭元素", "关闭坐标", "检测方式"],
            [
                [
                    row["surface"],
                    row["reasons"],
                    row["close_element"],
                    row["close_center"],
                    row["detection"],
                ]
                for row in data["forbidden"]
            ],
        )
    )
    lines.extend(
        [
            "停机界面族与处置策略：",
            "",
            f"- 停机界面族 `{_json(data['stop_surfaces'])}`",
            f"- 红线界面策略 `{data['policies']['forbidden_surface_policy']}`",
            f"- 关闭策略 `{data['policies']['stop_surface_close_policy']}`",
            f"- 未识别界面 `{data['policies']['unrecognized_surface']}`",
            "",
            "### 2.7 已验证路径",
            "",
            f"- 目标路径 `{_json(data['route']['goal'])}`",
            f"- 已验证前缀 `{_json(data['route']['verified_prefix'])}`",
            "",
        ]
    )
    lines.extend(_registry_section(data)[:2])
    keys = data.get("signature_anchor_keys") or {}
    lines.extend(
        [
            "### 3.1 设施缺陷：签名锚点的键名有两种写法",
            "",
            "这一条不是游戏的设计事实，是识别设施自己的缺陷，放在推断与未知层：签名锚点的颜色"
            f"可以写在 `{keys.get('canonical_key')}` 或 `average_rgb` 两个键名下，而加载器一度"
            "只认前者，写成后者的签名被静默编译成 0 个锚点——像素通道对这些界面从未运行过，"
            "它们能被认出只是因为「先按名字识别、再按名字查红线集」的后备路径还在。加载器现在"
            "两种键名都接受，并且在「声明了签名却编译出 0 锚点」时于加载期报错。下表是当前登记"
            "里两种键名各自的锚点数，以及写成 `average_rgb` 的那些界面。",
            "",
            f"- 锚点键名分布 `{_json(keys.get('key_forms') or {})}`",
            "",
        ]
    )
    lines.extend(
        _table(
            ["界面", "写成 average_rgb 的锚点数", "是否红线界面"],
            [
                [row["surface"], row["anchors"], row["forbidden"]]
                for row in keys.get("alternate_key_surfaces") or []
            ],
            aligns="lrl",
        )
    )
    lines.extend(["### 3.2 未消化的登记观测", ""])
    lines.extend(_registry_section(data)[2:])
    return lines


def render_onboarding_and_rules(data: dict[str, Any]) -> list[str]:
    declared = data.get("declared") or {}
    lines = [
        LAYER_CONFIG,
        "",
        "引导链的数据表是 `TutorialConfig`：每行一个引导步，字段为 `id` / `desc` / "
        "`condition` / `executer`。`executer` 指向同名的引导执行类，分步行为写在代码里、"
        "不在表里；`condition` 为空表示进入游戏后即可由所属系统事件触发。"
        "规则文案本身不在本表内，见第 2 层与第 3 层。",
        "",
        "### 1.1 按 executer 家族分组",
        "",
        "家族取 `executer` 去掉下划线后缀与末尾步序号后的部分，"
        "所以 `Beginning1` / `Beginning2` 同族，`HitBricks_west` 归到 `HitBricks`。",
        "",
    ]
    lines.extend(
        _table(
            ["家族", "步数"],
            [[row["family"], row["step_count"]] for row in declared.get("families") or []],
            aligns="lr",
        )
    )
    lines.extend(
        [
            "### 1.2 条件文法与取值集合",
            "",
            _CONDITION_GRAMMAR,
            "",
            "「引用的步」列只列出该子句的前几个引用者，完整归属看 1.3 的全表。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["条件类型", "参数", "引用步数", "引用的步", "触发时机（推断）"],
            [
                [
                    row["type"] or "（空条件）",
                    row["arguments"],
                    row["reference_count"],
                    _cell(row["tutorial_ids"]) + ("…" if row.get("truncated") else ""),
                    row["forecast"],
                ]
                for row in data.get("conditions") or []
            ],
            aligns="llrll",
        )
    )
    lines.extend(["### 1.3 引导链全表", ""])
    lines.extend(
        _table(
            ["引导步 id", "说明", "触发条件", "executer", "家族"],
            [
                [row["tutorial_id"], row["desc"], row["condition"], row["executer"], row["family"]]
                for row in declared.get("rows") or []
            ],
        )
    )
    if declared.get("duplicate_ids"):
        lines.extend(
            [
                f"表内有重复的引导步 id：`{_json(declared['duplicate_ids'])}`；"
                "运行时按 id 建索引，后出现的行会覆盖先出现的行。",
                "",
            ]
        )
    lines.extend([LAYER_LIVE, "", "### 2.1 引导步与实机登记的对应", ""])
    lines.extend(
        _table(
            ["引导步 id", "executer", "说明", "实机登记", "对上的依据", "证据帧", "存档状态"],
            [
                [
                    row["tutorial_id"],
                    row["executer"],
                    row["desc"],
                    f"{row['live_kind']}.{row['live_name']}",
                    row["basis"],
                    row["evidence_frames"],
                    row["save_state"],
                ]
                for row in data.get("matched") or []
            ],
        )
    )
    lines.extend(
        [
            "### 2.2 实机已登记但表内无对应行",
            "",
            "下列登记项带引导手指或引导语义，但在引导链表里找不到能对上的行。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["实机登记", "登记状态", "证据帧"],
            [
                [f"{row['live_kind']}.{row['live_name']}", row["status"], row["evidence_frames"]]
                for row in data.get("unmatched_live") or []
            ],
        )
    )
    save_state = data.get("save_state") or {}
    lines.extend(["### 2.3 存档记录的引导状态", ""])
    if save_state.get("available"):
        lines.extend(
            _table(
                ["口径", "值"],
                [
                    ["存档来源", save_state.get("source")],
                    ["密文 sha256", save_state.get("encrypted_sha256")],
                    ["状态枚举真源", save_state.get("state_truth")],
                    ["存档内引导行数", save_state.get("row_count")],
                    ["各状态计数", save_state.get("state_counts")],
                    ["存档有、表内无的 id 数", len(data.get("foreign_save_ids") or [])],
                    ["快照当时的账号进度", save_state.get("snapshot_progress")],
                ],
            )
        )
        lines.extend(
            [
                "存档只记录已经启动过的引导步：条件满足时写入 `Ongoing`，完成时改写 "
                "`Finished`，没启动过的步在存档里没有行。两边的差集要分开读——"
                "存档有、表内无的 id，和表内有、存档无且本来无条件的步，"
                "指向同一件事：实机跑的引导表与本次反编译的不是同一版。"
                "这份快照取自长跑开始前，账号等级低于后来实机走到的等级，"
                "所以 2.1 里有几行「实机已对上」却在快照里查无此行——那是快照更早，不是矛盾。",
                "",
                "#### 2.3.1 存档已完成、但本账号观测期内未再出现的步",
                "",
            ]
        )
        lines.extend(
            _table(
                ["引导步 id", "executer", "说明", "触发条件"],
                [
                    [row["tutorial_id"], row["executer"], row["desc"], row["condition"]]
                    for row in data.get("save_only_finished") or []
                ],
            )
        )
        lines.extend(["#### 2.3.2 存档有、本版引导表内无对应行的 id", ""])
        lines.extend(
            _table(
                ["引导步 id"],
                [[identifier] for identifier in data.get("foreign_save_ids") or []],
            )
        )
    else:
        lines.extend(["未提供存档投影，本节为空。", ""])
    lines.extend(["### 2.4 教程链步骤登记", ""])
    rows = []
    for chain in data["chains"]:
        for index, step in enumerate(chain["steps"], start=1):
            rows.append(
                [
                    chain["chain"],
                    index,
                    step["name"],
                    step["guided_target"],
                    step["target_semantic"],
                    step["anchor_count"],
                    step["evidence_frame"],
                ]
            )
    lines.extend(
        _table(
            ["教程链", "步", "步骤名", "引导坐标", "目标语义", "签名锚点数", "证据帧"],
            rows,
            aligns="lrlllrl",
        )
    )
    lines.extend(["### 2.5 教程界面与关闭尝试", ""])
    lines.extend(
        _table(
            ["界面", "类别", "登记状态", "已记录文案与特征", "关闭尝试", "证据帧数"],
            [
                [
                    surface["surface"],
                    surface["kind"],
                    surface["status"],
                    surface["features"],
                    surface["dismiss_attempts"],
                    len(surface["evidence_frames"]),
                ]
                for surface in data["tutorial_surfaces"]
            ],
            aligns="lllllr",
        )
    )
    lines.extend(
        [
            "#### 2.5.1 教程期间的输入遮罩与唯一出口",
            "",
            "强引导不是「建议」：执行器会把可点区域缩到引导目标那一个控件，其余输入连同系统返回键"
            "一起失效，这一步只能由它自己声明的完成事件结束。下表是每个已登记教程界面的处置说明"
            "与已验证的控件结果。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["界面", "允许动作", "保留动作", "处置说明"],
            [
                [
                    surface["surface"],
                    surface["allowed_actions"],
                    surface["withheld_actions"],
                    surface["disposition"],
                ]
                for surface in data["tutorial_surfaces"]
            ],
        )
    )
    lines.extend(
        _table(
            ["界面", "控件", "已验证结果"],
            [
                [surface["surface"], row["element"], row["outcome"]]
                for surface in data["tutorial_surfaces"]
                for row in surface["verified_outcomes"]
            ],
        )
    )
    claim = data.get("tutorial_free_claim") or {}
    if claim:
        lines.extend(
            [
                "教程期间唯一一次免费领取的存档差分（该教程只能由领取事件结束，X 与返回键都无效）：",
                "",
            ]
        )
        lines.extend(
            _table(
                ["口径", "值"],
                [
                    ["界面", claim["surface"]],
                    ["领取过一次", claim["claimed_once"]],
                    ["理由", claim["reason"]],
                    ["体力 前→后", f'{claim["energy"][0]}→{claim["energy"][1]}'],
                    ["宝石 前→后", f'{claim["gems"][0]}→{claim["gems"][1]}'],
                    ["金币 前→后", f'{claim["coins"][0]}→{claim["coins"][1]}'],
                    ["证据帧 sha256", claim["evidence_frame_sha256"]],
                    ["反编译依据", claim["config_refs"]],
                    ["来源", claim["source"]],
                ],
                aligns="ll",
            )
        )
    lines.extend(
        [
            "### 2.6 规则与引导文案",
            "",
            "取自界面登记里带中文的特征串，即帧上实际读到的文字；未做文字识别的部分不在此列。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["界面", "文案", "证据帧"],
            [[row["surface"], row["text"], row["evidence_frame"]] for row in data["rule_texts"]],
        )
    )
    lines.extend(["### 2.7 玩法观测事实", ""])
    lines.extend(
        _table(
            ["fact id", "观测", "证据"],
            [
                [row["fact_id"], row["observation"] or row["key"], row["evidence_refs"]]
                for row in data["observations"]
            ],
        )
    )
    lines.extend(localization_block(data, "### 2.8 引导表的本地化键"))
    lines.extend(_registry_section(data))
    lines.extend(
        [
            "### 3.1 表内有、本账号尚未走到的步",
            "",
            "「触发时机」是按条件类型推出来的说法，不是观测；存档状态那一列才是证据。"
            "`Ongoing` 表示条件已经满足过、引导已经启动但没有走完。"
            "`存档无该行` 对有条件的步表示条件一次都没满足过；"
            "对无条件的步则说明实机跑的引导表与本次反编译的不是同一版，"
            "反向差集见 2.3.2。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["引导步 id", "executer", "家族", "说明", "触发条件", "存档状态", "触发时机（推断）"],
            [
                [
                    row["tutorial_id"],
                    row["executer"],
                    row["family"],
                    row["desc"],
                    row["condition"],
                    row["save_state"],
                    row["forecast"],
                ]
                for row in data.get("untriggered") or []
            ],
        )
    )
    return lines


def _free_claim_section(data: dict[str, Any]) -> list[str]:
    """The free-claim rule, what it permits per surface, and the audit of it."""

    policy = data.get("free_claim") or {}
    if not policy.get("available"):
        return ["### 2.5 free-claim 口径", "", "本次生成没有读到审计产物，未渲染。", ""]
    audit = policy["audit"]
    deviation = policy["deviation"]
    lines = [
        "### 2.5 free-claim 口径与审计",
        "",
        "「免费」两个字不是判据，界面才是：同一个免费按钮放在无条件发放层里可以点，"
        "放在商店或购买面板里就不能点。规则与逐条审计都由仪表按同一判据产出，本节只搬结果。",
        "",
        f"- 判据：{policy['criterion']}",
        f"- 允许：{policy['unconditional_grants']}",
        f"- 商店高亮免费礼物：{policy['shop_gift']}",
        f"- 口径来源：{policy['policy_source']}；生效时刻 {policy['policy_effective_at']}",
        f"- 口径改前的偏差：{deviation['what']}（原规则：{deviation['against']}）；"
        f"裁定：{deviation['ruling']}；其间落在钱/广告/登录/外链上的点击 {deviation['redline_taps']} 次",
        f"- 审计来源 `{policy['source']}`",
        "",
        "#### 2.5.1 逐控件处置",
        "",
        "判据逐条落到勘测登记的控件上：控件必须有 verified_outcome，所在界面不能是红线界面，"
        "也不能带价目录。",
        "",
    ]
    lines.extend(
        _table(
            ["界面", "控件", "已验证结果", "在红线界面内", "界面带价目录", "处置"],
            [
                [
                    row["surface"],
                    row["element"],
                    row["verified_outcome"],
                    row["in_forbidden_surface"],
                    row["priced_catalog"],
                    row["verdict"],
                ]
                for row in policy["dispositions"]
            ],
            aligns="llllll",
        )
    )
    lines.extend(["#### 2.5.2 审计计数", ""])
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["审计事件数", audit["events"]],
                ["其中领取", audit["claims"]],
                ["其中关闭", audit["closes"]],
                ["判为合规", audit["compliant"]],
                ["带存档差分的领取", audit["claims_with_save_diff"]],
                ["不带存档差分的领取", audit["claims_without_save_diff"]],
                ["标红", audit["flagged"]],
                ["其中在口径生效前", audit["flagged_before_policy"]],
                ["其中在口径生效后", audit["flagged_after_policy"]],
                ["标红原因分布", audit["flagged_by_reason"]],
                ["已登记界面数", audit["surfaces_registered"]],
            ],
            aligns="ll",
        )
    )
    lines.extend(["#### 2.5.3 标红逐条", ""])
    lines.extend(
        _table(
            ["界面", "控件", "类别", "来源报告", "在口径生效前", "判定依据", "标红原因", "有存档差分"],
            [
                [
                    row["screen"],
                    row["element"],
                    row["kind"],
                    row["report"],
                    row["before_policy"],
                    row["era_basis"],
                    row["reasons"],
                    row["save_diff_available"],
                ]
                for row in policy["flagged_rows"]
            ],
        )
    )
    return lines


def render_monetization_touchpoints(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "商品目录、价格字段与仓库扩容价的配置声明层在"
        "`monetization-shop-iap-cn` 里；本文档只描述实机出现的触点，不重复配置表。",
        "",
        LAYER_LIVE,
        "",
        "### 2.1 触点",
        "",
        "分类只看登记的控件名与红线原因，不看特征文字：`real-money` 是登记标注的真钱触点；"
        "`store-route` 是通向应用商店或外链的弹窗；`soft-currency-price` 是登记了价格控件的界面；"
        "`free-claim` 是只登记了领取类控件的界面。",
        "",
    ]
    lines.extend(
        _table(
            ["界面", "分类", "登记状态", "内容与特征", "允许的元素", "禁止点击的元素", "登记表编号"],
            [
                [
                    row["surface"],
                    row["category"],
                    row["status"],
                    row["features"],
                    row["allowed_elements"],
                    row["withheld_elements"],
                    row["registry"],
                ]
                for row in data["touchpoints"]
            ],
        )
    )
    lines.extend(["### 2.2 商店目录截图记录", ""])
    lines.extend(
        _table(
            ["界面", "分区", "条目", "刷新"],
            [[row["surface"], row["section"], row["item"], row["refresh"]] for row in data["catalog"]],
        )
    )
    lines.extend(["### 2.3 仓库扩容与点击纪律", ""])
    storage = data["storage_expansion"]
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["扩容价（宝石）", storage["add_slot_cost_gems"]],
                ["扩容控件坐标", storage["add_slot_control"]],
                ["是否允许点击", storage["add_slot_allowed"]],
                ["仓库登记状态", storage["status"]],
                ["计价商品点击数", data["click_discipline"]["priced_product_clicks"]],
                ["刷新点击数", data["click_discipline"]["refresh_clicks"]],
            ],
            aligns="lr",
        )
    )
    lines.extend(["### 2.4 证据帧", ""])
    lines.extend(
        _table(
            ["界面", "证据帧"],
            [
                [row["surface"], frame]
                for row in data["touchpoints"]
                for frame in row["evidence_frames"]
            ],
        )
    )
    lines.extend(_free_claim_section(data))
    lines.extend(_registry_section(data))
    return lines


def render_activity_entry_map(data: dict[str, Any]) -> list[str]:
    lines = _no_config_layer(
        "活动族配置表尚未纳入提炼（活动板、挖宝、消消等表在配置目录里仍是未归属项），"
        "本文档的配置声明层为空。"
    )
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "### 2.1 活动条目",
            "",
            f"结构字段：`{_json(data['structure'])}`。未观测到的字段写"
            "「未知（未观测）」，不做推测填充。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["活动", "登记状态", "入口坐标", "入口语义", "进度", "奖励", "起止", "触发", "登记表编号"],
            [
                [
                    row["activity"],
                    row["status"],
                    row["entry_control"],
                    row["entry_semantic"],
                    row["progress"],
                    row["reward"],
                    row["window"],
                    row["trigger"],
                    row["registry"],
                ]
                for row in data["activities"]
            ],
        )
    )
    lines.extend(["### 2.2 相关界面与证据帧", ""])
    lines.extend(
        _table(
            ["活动", "证据来源", "证据帧"],
            [
                [row["activity"], item["source"], item["frame"]]
                for row in data["activities"]
                for item in row["evidence_frames"]
            ],
        )
    )
    lines.extend(_registry_section(data))
    return lines


def _projection_section(data: dict[str, Any]) -> list[str]:
    """Days to the next task and to the recap gate, at the measured yield."""

    projection = data.get("projection") or {}
    if not projection.get("available"):
        return ["### 2.4 进度预计", "", "本次生成没有读到预计报告，未渲染。", ""]
    windows = projection.get("windows") or [projection]
    latest = windows[0]
    lines = [
        "### 2.4 进度预计（按当前收益率外推）",
        "",
        "主线每一步的门槛是金币，不是等级：任务表里每行写死 cost（金币），而等级只由任务的 "
        "exp 奖励被动推高。把「一点体力换多少金币」乘上一天能自然回复的体力，就得到一天能推"
        "多少金币，再除任务的金币缺口即为天数。",
        "",
        f"公式：`{latest['formula']}`。语料每长一截就重算一次，下面按报告时间从新到旧并列："
        "同一个公式、不同的样本数与不同时刻的手上金币，**以最新一次为准**，旧的一次留着看"
        "估计怎么动。",
        "",
        "输入（每列一次重算）：",
        "",
    ]
    input_rows: list[list[Any]] = [
        ["来源报告", *(window["source"] for window in windows)],
        ["取自报告数", *(window["observed"]["reports_used"] for window in windows)],
        ["确认花掉的体力", *(window["observed"]["energy_spent_confirmed"] for window in windows)],
        ["确认的交付金币", *(window["observed"]["delivery_coins_confirmed"] for window in windows)],
        ["确认的交付次数", *(window["observed"]["confirmed_deliveries"] for window in windows)],
        ["金币/体力", *(window["observed"]["coins_per_energy"] for window in windows)],
        ["起点主线任务", *(window["inputs"]["from_task"] for window in windows)],
        ["目标主线任务", *(window["inputs"]["to_task"] for window in windows)],
        ["手上金币", *(window["inputs"]["coins_on_hand"] for window in windows)],
        [
            "等级 / xp",
            *(f'{window["inputs"]["level"]} / {window["inputs"]["xp"]}' for window in windows),
        ],
        [
            "体力恢复周期（秒）",
            *(window["inputs"]["energy_recovery_seconds"] for window in windows),
        ],
        ["一天可回复体力", *(window["energy_per_day"] for window in windows)],
        ["一天可得金币", *(window["coins_per_day"] for window in windows)],
        ["配置引用", *(window["inputs"]["config_refs"] for window in windows)],
    ]
    headers = ["输入"] + [
        f"第 {index} 新" if index > 1 else "最新" for index in range(1, len(windows) + 1)
    ]
    lines.extend(_table(headers, input_rows, aligns="l" * (len(windows) + 1)))
    lines.extend(["预计：", ""])
    projection_rows: list[list[Any]] = []
    for window in windows:
        next_task = window["next_task"]
        target = window["target"]
        projection_rows.append(
            [
                next_task["task"],
                1,
                next_task["cost"],
                next_task["coins_short"],
                next_task["days"],
                "",
                "",
                window["observed"]["reports_used"],
                window["source"],
            ]
        )
        projection_rows.append(
            [
                target["task"],
                target["tasks_inclusive"],
                target["coins_needed"],
                target["coins_short"],
                target["days"],
                f'{target["level_on_arrival"]}（+{target["xp_into_that_level"]} xp）',
                target["task_exp_granted"],
                window["observed"]["reports_used"],
                window["source"],
            ]
        )
    lines.extend(
        _table(
            [
                "目标",
                "任务数（含）",
                "所需金币",
                "金币缺口",
                "预计天数",
                "到达时等级",
                "任务 exp 合计",
                "取自报告数",
                "来源",
            ],
            projection_rows,
            aligns="lrrrrlrrl",
        )
    )
    return lines


def render_progression_live(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "第 1 天与第 2 天的主线任务声明，来源为主线任务表；完整序列在"
        "`progression-main-tasks`。",
        "",
    ]
    lines.extend(
        _table(
            ["任务", "配置名", "声明 cost", "声明 rewards", "角色图标"],
            [
                [row["task"], row["name"], row["cost"], row["rewards"], row["detail_icon"]]
                for row in data["declared_head"]
            ],
            aligns="llrll",
        )
    )
    lines.extend([LAYER_LIVE, "", "### 2.1 本账号主线曲线", ""])
    lines.extend(
        _table(
            [
                "任务",
                "声明 cost",
                "面板显示 cost",
                "实机金币消耗",
                "一致",
                "完成时间",
                "等级",
                "体力",
                "fact id",
                "证据来源",
            ],
            [
                [
                    row["task"],
                    row["declared_cost"],
                    row["displayed_cost"],
                    row["observed_coin_cost"],
                    row["consistent"],
                    row["captured_at"],
                    f'{row["level_before"]}→{row["level_after"]}'
                    if row["level_after"] is not None
                    else row["level_before"],
                    f'{row["energy_before"]}→{row["energy_after"]}'
                    if row["energy_after"] is not None
                    else row["energy_before"],
                    row["fact_id"],
                    row["evidence_source"],
                ]
                for row in data["steps"]
            ],
            aligns="lrrrlllllll",
        )
    )
    lines.extend(
        [
            f"面板 cost 序列：`{_json(data['cost_sequence'])}`。",
            "",
            "### 2.2 等级跃迁",
            "",
        ]
    )
    lines.extend(
        _table(
            ["等级", "声明升级 cost", "跃迁前 xp", "任务 exp 奖励", "跃迁后 xp", "与声明相符", "fact id"],
            [
                [
                    f'{row["from_level"]}→{row["to_level"]}',
                    row["declared_cost"],
                    row["xp_before"],
                    row["task_exp_reward"],
                    row["xp_after"],
                    row["matches_declared_cost"],
                    row["fact_id"],
                ]
                for row in data["level_steps"]
            ],
            aligns="lrrrrll",
        )
    )
    lines.extend(["### 2.3 金币与体力轨迹（按证据来源报告分组）", ""])
    lines.extend(
        _table(
            ["来源报告", "首个事实", "末个事实", "事实数", "金币 起→止", "体力 区间"],
            [
                [
                    row["report"],
                    row["first_seen"],
                    row["last_seen"],
                    row["fact_count"],
                    f'{row["coins_first"]}→{row["coins_last"]}'
                    if row["coins_first"] is not None
                    else "",
                    f'{row["energy_min"]}–{row["energy_max"]}'
                    if row["energy_min"] is not None
                    else "",
                ]
                for row in data["runs"]
            ],
            aligns="lllrll",
        )
    )
    lines.extend(_projection_section(data))
    lines.extend(["### 2.5 长跑时间线", ""])
    if data["long_run"]:
        headers = list(data["long_run"][0].keys())
        lines.extend(
            _table(headers, [[row.get(name) for name in headers] for row in data["long_run"]])
        )
    else:
        lines.extend(["（无记录）", ""])
    lines.extend(_registry_section(data))
    return lines


def render_telemetry_bi_events(data: dict[str, Any]) -> list[str]:
    lines = [
        LAYER_CONFIG,
        "",
        "本文档整层都是代码声明：字段表来自反编译的埋点管理器，"
        f"实机截获状态为「{data['capture_status']}」。",
        "",
        "### 1.1 事件字段",
        "",
        f"计划要求的核心事件：`{_json(data['core_events'])}`。",
        "",
    ]
    lines.extend(
        _table(
            ["event", "声明函数", "字段", "常量字段", "核心事件"],
            [
                [
                    row["event"],
                    row["function"],
                    row["fields"],
                    row["constants"],
                    row["event"] in data["core_events"],
                ]
                for row in data["events"]
            ],
        )
    )
    lines.extend(["### 1.2 每条事件附带的公共字段", ""])
    lines.extend(
        _table(
            ["字段族", "字段"],
            [
                ["账号快照（可关闭）", data["bag_fields"]],
                ["信封", data["envelope_fields"]],
                ["设备信息（仅初始化事件）", data["device_fields"]],
            ],
        )
    )
    lines.extend(
        [
            LAYER_LIVE,
            "",
            "没有实机截获记录：本轮未落地网络侧采集，事件与字段均未在设备上验证。",
            "",
        ]
    )
    lines.extend(_registry_section(data))
    return lines


def _packaged_story_section(packaged: dict[str, Any]) -> list[str]:
    """Render the unpacked story assets,证据等级 decompiled_asset."""

    if not packaged.get("available"):
        return [
            "### 1.5 随包剧情脚本",
            "",
            "本次生成没有解包产物，随包剧情脚本一节为空。",
            "",
        ]
    lines = [
        "### 1.5 随包剧情脚本（证据等级 decompiled_asset）",
        "",
        "主线剧情脚本与其文案成对存放在 "
        "`Assets/DynamicConfig/Story/<前缀>/{Story,GameText}_<前缀>.lua.bytes`，"
        "一个前缀一个资源包。目录清单声明的前缀里只有一部分随包落地，其余按前缀走 CDN；"
        "本机资源缓存里另有已下发的前缀。取得的载荷都不是明文 Lua，"
        f"统一以 {packaged.get('encrypted_header_bytes')} 字节固定头开始，"
        f"头部为 `{packaged.get('encrypted_header_hex')}`；"
        "本任务只记录这一形态并原样留存载荷，不做解密，因此逐句脚本句数为 "
        f"{packaged.get('parsed_sentence_count')}。",
        "",
    ]
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["目录清单声明的前缀数", packaged.get("catalog_prefix_count")],
                ["随包前缀数", packaged.get("packaged_prefix_count")],
                ["CDN 已下发并缓存的前缀数", packaged.get("cdn_cached_prefix_count")],
                ["已取得前缀数", packaged.get("obtained_prefix_count")],
                ["未取得前缀数", packaged.get("not_obtained_prefix_count")],
                ["明文载荷数", packaged.get("plaintext_asset_count")],
                ["加密载荷数", packaged.get("encrypted_asset_count")],
                ["解析出的句数", packaged.get("parsed_sentence_count")],
            ],
        )
    )
    lines.extend(["#### 1.5.1 已取得的前缀", ""])
    lines.extend(
        _table(
            ["前缀", "来源", "载荷数", "字节数", "载荷形态", "解析句数"],
            [
                [
                    entry["prefix"],
                    entry["origin"],
                    entry["asset_count"],
                    entry["bytes"],
                    entry["status"],
                    entry["parsed_sentences"],
                ]
                for entry in packaged.get("prefixes") or []
            ],
            aligns="llrrlr",
        )
    )
    return lines


def render_story_script(data: dict[str, Any]) -> list[str]:
    recap = data.get("recap") or {}
    explore = data.get("explore") or {}
    check = data.get("speaker_check") or {}
    localization = data.get("localization") or {}
    packaged = data.get("packaged_story") or {}
    lines = [
        LAYER_CONFIG,
        "",
        "剧情有三张可读的结构表：主线任务表的角色图标字段、剧情回顾表 "
        f"`{recap.get('table')}`、地图探险对话表 `{explore.get('table')}`。"
        "两张剧情表的 `Content` 都是本地化键而不是文本；正文取自随包的 "
        f"`{localization.get('source_asset')}`，已在下面逐行换出。",
        "",
        "### 1.1 主线任务的角色图标",
        "",
    ]
    lines.extend(
        _table(
            ["角色图标", "关联任务数"],
            [[row["character"], row["task_count"]] for row in data["characters"][:20]],
            aligns="lr",
        )
    )
    lines.extend(
        [
            "### 1.2 剧情回顾结构",
            "",
            "一行是一条回顾内容；`Book` 是分册，`Task` 是该册的解锁主线任务，"
            "`Page` 是册内页码，`Type` 分 title / subtitle / text / img 四类。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["表行数", recap.get("row_count")],
                ["分册数", recap.get("book_count")],
                ["Book × Task 分组数", recap.get("group_count")],
                ["各类型行数", recap.get("type_counts")],
            ],
        )
    )
    lines.extend(["#### 1.2.1 分册", ""])
    lines.extend(
        _table(
            ["分册", "解锁主线任务", "页数", "行数", "各类型行数"],
            [
                [
                    book["book"],
                    book["unlock_task"],
                    book["page_count"],
                    book["row_count"],
                    book["type_counts"],
                ]
                for book in recap.get("books") or []
            ],
            aligns="rlrrl",
        )
    )
    lines.extend(["#### 1.2.2 逐页", ""])
    lines.extend(
        _table(
            ["分册", "页", "标题键", "标题", "副标题键", "副标题", "本页行数", "各类型行数"],
            [
                [
                    page["book"],
                    page["page"],
                    page["title_key"],
                    page.get("title_text") or MISSING,
                    page["subtitle_key"],
                    page.get("subtitle_text") or MISSING,
                    page["row_count"],
                    page["type_counts"],
                ]
                for page in recap.get("pages") or []
            ],
            aligns="rrllllrl",
        )
    )
    lines.extend(
        [
            "#### 1.2.3 逐行正文",
            "",
            "`text` / `title` / `subtitle` 三类行的 `Content` 是本地化键，下表按键换出正文；"
            "`img` 类行的 `Content` 是图片资产名，不参与换出，单列一行统计。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["文本类行数", recap.get("text_bearing_row_count")],
                ["已换出正文行数", recap.get("resolved_row_count")],
                ["文本类命中率（%）", recap.get("resolved_rate_percent")],
                ["全表命中率（%）", recap.get("all_row_resolved_rate_percent")],
                ["img 类行数（不是文本键）", recap.get("non_text_row_count")],
                ["未命中的文本键", recap.get("unresolved_keys")],
            ],
        )
    )
    lines.extend(
        _table(
            ["分册", "页", "类型", "文本键", "正文"],
            [
                [line["book"], line["page"], line["type"], line["key"], line["text"] or MISSING]
                for line in recap.get("lines") or []
                if line.get("text_bearing")
            ],
            aligns="rrlll",
        )
    )
    lines.extend(
        [
            "### 1.3 地图探险对话结构",
            "",
            "一行是一句对话：`Id` 是一段对话，`Step` 是句序，`Speaker` 是说话人，"
            "`Position` 1 为左侧、2 为右侧，`Animation` 是说话人骨骼动画名，"
            "`BubbleType` 是气泡样式。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["表行数", explore.get("row_count")],
                ["对话段数", explore.get("dialogue_count")],
                ["说话人分布", explore.get("speaker_counts")],
                ["位置分布", explore.get("position_counts")],
                ["动画分布", explore.get("animation_counts")],
                ["气泡样式分布", explore.get("bubble_type_counts")],
            ],
        )
    )
    lines.extend(["#### 1.3.1 逐段", ""])
    lines.extend(
        _table(
            ["对话段", "句数", "句序", "说话人", "位置", "动画", "气泡样式", "文本键"],
            [
                [
                    row["dialogue_id"],
                    row["step_count"],
                    row["steps"],
                    row["speakers"],
                    row["positions"],
                    row["animations"],
                    row["bubble_types"],
                    row["content_keys"],
                ]
                for row in explore.get("dialogues") or []
            ],
            aligns="lrllllll",
        )
    )
    lines.extend(
        [
            "#### 1.3.2 逐句正文",
            "",
            "对话正文与说话人名都经 `GameTextModel:GetText` 换出，说话人名的键是 "
            "`<Speaker>_name`。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["句数", explore.get("row_count")],
                ["已换出正文句数", explore.get("resolved_row_count")],
                ["命中率（%）", explore.get("resolved_rate_percent")],
                ["未命中的文本键", explore.get("unresolved_keys")],
            ],
        )
    )
    lines.extend(
        _table(
            ["对话段", "句序", "说话人", "说话人名", "文本键", "正文"],
            [
                [
                    line["dialogue_id"],
                    line["step"],
                    line["speaker"],
                    line.get("speaker_name") or MISSING,
                    line["key"],
                    line["text"] or MISSING,
                ]
                for line in explore.get("lines") or []
            ],
            aligns="lrllll",
        )
    )
    lines.extend(
        [
            "### 1.4 说话人取值与说话人表的包含关系",
            "",
            f"校验对象是 `{check.get('declared_table')}` 的角色键集合。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["说话人表角色数", check.get("declared_count")],
                ["对话表用到的说话人", check.get("used_speakers")],
                ["用到但未声明的说话人", check.get("missing_from_declaration")],
                ["是否为子集", check.get("subset")],
                ["有随包名字的角色数", check.get("named_count")],
                ["名字命中率（%）", check.get("named_rate_percent")],
                ["随包文本里没有名字的角色", check.get("unnamed_speakers")],
            ],
        )
    )
    lines.extend(
        _table(
            ["角色键", "名字键", "名字"],
            [
                [entry["speaker"], entry["name_key"], entry["name_text"] or MISSING]
                for entry in check.get("names") or []
            ],
        )
    )
    lines.extend(_packaged_story_section(packaged))
    lines.extend([LAYER_LIVE, "", "### 2.1 已走过的剧情界面", ""])
    lines.extend(
        _table(
            ["界面", "登记状态", "特征", "推进元素", "变体数", "证据帧数"],
            [
                [
                    scene["surface"],
                    scene["status"],
                    scene["features"],
                    scene["advance_elements"],
                    len(scene["variants"]),
                    len(scene["evidence_frames"]),
                ]
                for scene in data["scenes"]
            ],
            aligns="lllrrr",
        )
    )
    lines.extend(["### 2.2 已登记变体", ""])
    lines.extend(
        _table(
            ["界面", "变体", "证据帧"],
            [
                [scene["surface"], variant["name"], variant["frame"]]
                for scene in data["scenes"]
                for variant in scene["variants"]
            ],
        )
    )
    lines.extend(["### 2.3 证据帧", ""])
    lines.extend(
        _table(
            ["界面", "证据帧"],
            [
                [scene["surface"], frame]
                for scene in data["scenes"]
                for frame in scene["evidence_frames"]
            ],
        )
    )
    lines.extend(["### 2.4 装修选择与推进上限", ""])
    decoration = data["decoration_choice"]
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["装修选择策略", decoration["selection_policy"] or decoration["declared_policy"]],
                ["装修面板元素", decoration["options"]],
                ["剧情推进上限", data["advance_policy"]["story_continue_limit"]],
                ["主线任务开始上限", data["advance_policy"]["main_task_start_limit"]],
            ],
        )
    )
    progress = data.get("live_story_progress") or {}
    lines.extend(
        [
            "### 2.5 剧情回顾在本账号的解锁情况",
            "",
            "解锁判据来自剧情回顾模型：当前主线任务要走过该册的解锁任务，该册才可读。"
            "本账号走到的主线任务与最早一册的解锁任务如下。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["已结算的主线任务", progress.get("settled_tasks")],
                ["当前主线任务", progress.get("current_task")],
                ["最早一册的解锁任务", progress.get("earliest_unlock_task")],
                ["已解锁分册数", progress.get("unlocked_book_count")],
                ["已解锁分册", progress.get("unlocked_books")],
            ],
        )
    )
    lines.extend(
        _table(
            ["分册", "解锁主线任务", "是否已解锁", "距解锁还差的天数"],
            [
                [
                    book["book"],
                    book["unlock_task"],
                    book.get("unlocked"),
                    book.get("live_gap_days"),
                ]
                for book in (data.get("recap") or {}).get("books") or []
            ],
            aligns="rllr",
        )
    )
    lines.extend(
        [
            f"文本状态：{data['text_status']}。",
            "",
        ]
    )
    lines.extend(_registry_section(data))
    lines.extend(
        [
            "### 3.1 本地化文本的载体与仍然缺口的部分",
            "",
            "两张剧情表的 `Content` 与说话人名都要经 `GameTextModel:GetText` 换成正文，"
            "这一层由 C# 侧实现，反编译的 Lua 树里没有对应的文本表。"
            "界面与回顾文案的载体是 `Assets/Text/GameText/game_text_<语言>.json`，"
            "已解包并在第 1 层换出；主线剧情文案与脚本是 "
            "`Assets/DynamicConfig/Story/<前缀>/{GameText,Story}_<前缀>.lua.bytes`，"
            "已取得的载荷全部加密，形态记录在 1.5 节。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["口径", "值"],
            [
                ["随包语种数", localization.get("language_count")],
                ["目录清单声明的语种数", localization.get("catalog_language_count")],
                ["各语种键集合是否一致", localization.get("keys_identical_across_languages")],
                ["键集合完整的语种", localization.get("complete_languages")],
                ["键集合有缺口的语种", localization.get("incomplete_languages")],
                ["文本键总数", localization.get("key_count")],
                ["未取得的剧情前缀数", packaged.get("not_obtained_prefix_count")],
            ],
        )
    )
    lines.extend(
        [
            "随包只有中文与英文两份是全量的，其余语种少掉的键在运行时只能由 CDN 覆盖补齐；"
            "缺口按语种如下。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["语种", "键数", "比基准语种少的键数"],
            [
                [row["language"], row["key_count"], row["missing_vs_reference"]]
                for row in localization.get("language_shortfall") or []
            ],
            aligns="lrr",
        )
    )
    lines.extend(
        [
            "下表是两张剧情表逐列的键判定：一列只有在存在命中且没有任何单元格是现成中文时，"
            "才被判为本地化键列并换出正文。",
            "",
        ]
    )
    lines.extend(
        _table(
            ["表", "列", "有值行数", "命中文本表", "已是中文", "判为键列"],
            [
                [
                    scan["table"],
                    column["field"],
                    column["values"],
                    column["resolved"],
                    column["authored_text"],
                    column["substitutable"],
                ]
                for scan in localization.get("scans") or []
                for column in scan.get("columns") or []
            ],
            aligns="llrrrl",
        )
    )
    lines.extend(
        [
            "未取得的剧情前缀按目录清单列出，全部走 CDN 下发，本机既未随包也未缓存：",
            "",
            _cell(packaged.get("not_obtained_prefixes")),
            "",
        ]
    )
    return lines


LIVE_RENDERERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "ui-flow-network": render_ui_flow_network,
    "onboarding-and-rules": render_onboarding_and_rules,
    "monetization-touchpoints": render_monetization_touchpoints,
    "activity-entry-map": render_activity_entry_map,
    "progression-live": render_progression_live,
    "telemetry-bi-events": render_telemetry_bi_events,
    "story-script": render_story_script,
}
