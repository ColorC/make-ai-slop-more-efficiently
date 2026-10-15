# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Reading predefined map tokens out of the shipped tables.

Moved verbatim out of ``stage_compiler.py`` so that file fits the 2000-line
budget; the compiler imports both helpers, so behaviour is unchanged.

``predefines.tokenInsts`` supplies each token's alias, position and ``hidden``
flag; a wave action with ``actionType: 6`` names an alias and its tick comes off
the same relative clock the spawn compiler reproduces.  Stats come from
``character_table.json``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .entity_definitions import BloodDebtLedger, SummonDefinition
from .model_primitives import Direction
from .stage_clock import SPAWN_ACTION_TYPE, to_ticks

#: ``actionType`` of a wave action that activates a predefined token.
TOKEN_ACTIVATION_ACTION_TYPE = 6


def _compile_token_activations(stage: dict[str, Any], tps: int) -> dict[str, int]:
    """Absolute tick per activated alias, off the shipped relative clock.

    Deliberately mirrors `_compile_spawns`' nesting (wave preDelay, fragment
    preDelay, action preDelay) rather than re-deriving it, but does **not**
    advance the fragment's end: only spawn actions do that today, and changing
    it would move every existing spawn tick in every stage.  That reading is
    registered as pending, and a test pins the spawn ticks.

    An alias activated more than once keeps its earliest tick -- it can only
    enter play once.
    """

    ticks: dict[str, int] = {}
    wave_clock = 0.0
    for wave in stage.get("waves") or []:
        wave_clock += float(wave.get("preDelay") or 0)
        fragment_clock = wave_clock
        for fragment in wave.get("fragments") or []:
            fragment_clock += float(fragment.get("preDelay") or 0)
            fragment_end = fragment_clock
            for action in fragment.get("actions") or []:
                if int(action.get("actionType", -1)) != TOKEN_ACTIVATION_ACTION_TYPE:
                    if int(action.get("actionType", -1)) == SPAWN_ACTION_TYPE and action.get("key"):
                        pre = float(action.get("preDelay") or 0)
                        interval = float(action.get("interval") or 0)
                        count = max(1, int(action.get("count") or 1))
                        fragment_end = max(
                            fragment_end,
                            fragment_clock + pre + max(0, count - 1) * interval,
                        )
                    continue
                alias = str(action.get("key") or "")
                if not alias:
                    # 72 of the 484 activations carry an empty key; they name no
                    # token instance, so there is nothing to activate.  Left
                    # alone rather than guessed at.
                    continue
                tick = max(1, to_ticks(fragment_clock + float(action.get("preDelay") or 0), tps))
                if alias not in ticks or tick < ticks[alias]:
                    ticks[alias] = tick
            fragment_clock = fragment_end
        wave_clock = fragment_clock + float(wave.get("postDelay") or 0)
    return ticks


def _token_taunt_level(
    record: Mapping[str, Any], skill_table: Mapping[str, Any] | None
) -> int:
    """The taunt tier a device carries, from its skill or its base frame.

    盾卫's 列阵！ (`sktok_npcsld`) ships ``taunt_level: 1`` with ``duration -1``
    -- permanent -- and its description is 「更容易受到敌方的攻击」.  Read from
    the skill table rather than an id allow-list, so any other device carrying
    the same blackboard is covered.

    A skill tier wins when there is one; otherwise the attribute frame's
    ``tauntLevel`` is taken, **including its negative values**.  That half was
    missing, and it matters: of the 26 device kinds IS3 places, eight ship
    ``tauntLevel: -1`` -- the three 宝箱, 山海密语仪, 汲营枯枝,
    街头应急储备 and the two 动力装甲 -- and reading them as 0 put loot
    boxes and an enemy installation in the same target tier as the operators
    standing next to them, so an attacker would spend its swings on the box.
    """

    if skill_table:
        for entry in record.get("skills") or []:
            skill = skill_table.get(str(entry.get("skillId") or ""))
            if not skill:
                continue
            for level in skill.get("levels") or []:
                for item in level.get("blackboard") or []:
                    if str(item.get("key")) == "taunt_level":
                        return max(-1, min(3, int(float(item.get("value") or 0))))
    for phase in reversed(record.get("phases") or []):
        for frame in reversed(phase.get("attributesKeyFrames") or []):
            declared = (frame.get("data") or {}).get("tauntLevel")
            if isinstance(declared, (int, float)):
                return max(-1, min(3, int(declared)))
    return 0


def _token_death_effects(record: Mapping[str, Any]) -> tuple[int, int, int]:
    """``(life_point_cost, max_cost_gain, cost_gain)`` from a device's talent.

    Both figures are in the shipped tables, and both need the description to be
    read alongside the numbers, because the blackboard key names alone do not
    say **when** the effect fires:

    * 指挥终端 `trap_008_farm` -- talent description
      「摧毁时扣除 1 点关卡生命值」, blackboard `value: -1.0`.  `value` is a
      generic key (指挥终端's own *skill* uses it for a deploy-slot bonus), so
      the description carries the meaning and the sign carries the direction.
    * 街头应急储备 `trap_111_wdfarm` -- character description
      「被击败后恢复 5 点费用上限及费用」, blackboard `max_cost: 5.0`,
      `cost: 5.0`.  Its talent candidate ships **no** description at all, which
      is why the character-level one is consulted for the trigger.

    Surveyed against the data rather than assumed: of the 26 device kinds IS3
    places, six ship a talent blackboard at all, and only these two describe a
    death trigger.  The other four are 免疫控制效果 x2 (`range_radius`),
    山海密语仪 (an on-field enemy buff) and 尖刺宝箱 (`atk_scale`), none of which
    this reader touches.
    """

    character_description = str(record.get("description") or "")
    fires_on_defeat = "击败" in character_description
    life_point_cost = 0
    max_cost_gain = 0
    cost_gain = 0
    for talent in record.get("talents") or []:
        for candidate in talent.get("candidates") or []:
            values = {
                str(item.get("key")): item.get("value")
                for item in (candidate.get("blackboard") or [])
            }
            talent_description = str(candidate.get("description") or "")
            value = values.get("value")
            if (
                isinstance(value, (int, float))
                and value < 0
                and "生命值" in (talent_description + character_description)
            ):
                life_point_cost = max(life_point_cost, int(round(-float(value))))
            if not fires_on_defeat:
                continue
            for key, target in (("max_cost", "max"), ("cost", "cost")):
                amount = values.get(key)
                if not isinstance(amount, (int, float)) or amount <= 0:
                    continue
                if target == "max":
                    max_cost_gain = max(max_cost_gain, int(round(float(amount))))
                else:
                    cost_gain = max(cost_gain, int(round(float(amount))))
    return life_point_cost, max_cost_gain, cost_gain


def _token_enemy_aura(record: Mapping[str, Any]) -> tuple[int, int]:
    """``(attack_speed_points, move_speed_multiple_milli)`` while on the field.

    山海密语仪 `trap_096_gflag` is the one IS3 device that buffs the enemy
    side: talent description 「在场时，强化所有敌军的攻击速度与移动速度」
    with `attack_speed: 25.0` and `move_speed: 1.15`.

    The two numbers are in **different units** and the blackboard does not say
    which is which -- the recurring trap in this data.  PRTS 「山海密语仪」
    settles it: 「所有敌方单位的攻击速度+25，移动速度最终提升至115%
    （可无限叠加）」, i.e. 25 attack-speed **points** and a move speed
    **raised to** 115%, not increased by it.  Read 1.15 as a bonus and the six
    instances in 5-6 would hand the enemy side ×2.15 each.

    Gated on the description naming the enemy side, because `move_speed` and
    `attack_speed` are the two most reused keys in the whole topic and every
    other carrier of them buffs itself.
    """

    description = str(record.get("description") or "")
    for talent in record.get("talents") or []:
        for candidate in talent.get("candidates") or []:
            text = str(candidate.get("description") or "") + description
            if "敌军" not in text and "敌方" not in text:
                continue
            values = {
                str(item.get("key")): item.get("value")
                for item in (candidate.get("blackboard") or [])
            }
            points = values.get("attack_speed")
            multiple = values.get("move_speed")
            if not isinstance(points, (int, float)) or not isinstance(
                multiple, (int, float)
            ):
                continue
            if points < 0 or multiple <= 0:
                continue
            return int(round(float(points))), int(round(float(multiple) * 1000))
    return 0, 0


#: The shipped ``direction`` integer on a token instance, as a step in engine
#: coordinates (``col`` east, ``row`` as the map grid indexes it, unflipped).
#:
#: The order is the client's own ``Direction`` enum order, and it was **checked
#: against the placements rather than assumed**, because a wrong rotation is
#: invisible in the data and silently blows the airflow at the wrong tiles.  Of
#: the five candidate rotations, two put all 33 affected tiles inside the map;
#: of those two, this one covers 23 of the 33 with enemy route nodes and the
#: other only 17 -- and an airflow the enemies never walk through is not what
#: 「同向/逆向移动的敌人」 describes.
SHIPPED_DIRECTION_STEPS: dict[int, tuple[int, int]] = {
    0: (0, 1),
    1: (1, 0),
    2: (0, -1),
    3: (-1, 0),
}

SHIPPED_DIRECTIONS: dict[int, Direction] = {
    0: Direction.UP,
    1: Direction.RIGHT,
    2: Direction.DOWN,
    3: Direction.LEFT,
}

DIRECTION_STEPS: dict[Direction, tuple[int, int]] = {
    SHIPPED_DIRECTIONS[index]: step
    for index, step in SHIPPED_DIRECTION_STEPS.items()
}


def token_facing(token: Mapping[str, Any]) -> Direction | None:
    """The facing a shipped token instance declares, if it is a cardinal one."""

    declared = token.get("direction")
    if isinstance(declared, bool) or not isinstance(declared, int):
        return None
    return SHIPPED_DIRECTIONS.get(declared)


#: `blower_s_character[<relation>].atk` / `blower_s_enemy[<relation>].move_speed`
_AIRFLOW_KEY = re.compile(
    r"^blower_s_(?P<side>character|enemy)\[(?P<relation>equal|vertical|opposite)\]"
    r"\.(?P<stat>atk|move_speed)$"
)

#: 「向前方3格吹出气流」 -- the reach is in the skill's description text and
#: nowhere in its blackboard, so it is read from there rather than defaulted.
_AIRFLOW_REACH = re.compile(r"前方(?P<tiles>[0-9]+)格")


def _token_airflow(
    record: Mapping[str, Any], skill_table: Mapping[str, Any] | None
) -> dict[str, int]:
    """源石流发生装置's directional airflow, or an empty mapping.

    `sktok_blower` ships every figure the description promises, each scoped by
    side **and** by relation to the airflow:

        blower_s_character[equal].atk       +0.3
        blower_s_character[vertical].atk     0.0
        blower_s_character[opposite].atk    -0.3
        blower_s_enemy[equal].move_speed    +0.8
        blower_s_enemy[opposite].move_speed -0.5

    The signs settle the unit question that costs this topic so much: a
    resulting multiple cannot be -0.5, so these are bonus fractions -- ±30% attack
    and +80% / -50% movement.  `[vertical]` shipping exactly 0.0 is a real
    statement too, not a missing row: an operator across the flow is unaffected.
    """

    if not skill_table:
        return {}
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            found: dict[str, float] = {}
            for item in level.get("blackboard") or []:
                match = _AIRFLOW_KEY.match(str(item.get("key")))
                if match is None:
                    continue
                value = item.get("value")
                if isinstance(value, (int, float)):
                    found["%s_%s" % (match["side"], match["relation"])] = float(value)
            if not found:
                continue
            reach = _AIRFLOW_REACH.search(str(level.get("description") or ""))
            if reach is None:
                # The figures without their reach would be an airflow with no
                # extent -- reported by the caller, never guessed at.
                return {}
            return {
                "airflow_range_tiles": int(reach["tiles"]),
                "airflow_operator_attack_same_micros": round(
                    found.get("character_equal", 0.0) * 1_000_000
                ),
                "airflow_operator_attack_opposite_micros": round(
                    found.get("character_opposite", 0.0) * 1_000_000
                ),
                "airflow_enemy_move_same_micros": round(
                    found.get("enemy_equal", 0.0) * 1_000_000
                ),
                "airflow_enemy_move_opposite_micros": round(
                    found.get("enemy_opposite", 0.0) * 1_000_000
                ),
            }
    return {}


#: The skill every route obstacle carries, by name.  Four devices ship it --
#: 障碍物 `trap_001_crate` and 碎石 `trap_027_stone` in IS3, 战场废墟 and a
#: second 障碍物 elsewhere -- with the same text: 「改变敌人的行进路线」 plus
#: 「如果障碍阻挡了敌人，敌人会破坏此障碍」.  The name is the discriminator
#: because the two IS3 descriptions differ in wording (行进 against 行径) while
#: the skill they hang off is identical.
ROUTE_OBSTACLE_SKILL_NAME = "阻挡路线"


def _token_blocks_routes(
    record: Mapping[str, Any], skill_table: Mapping[str, Any] | None
) -> bool:
    """Whether this device reroutes enemies rather than fighting them.

    障碍物 / 碎石 change where enemies walk; the engine already models that as
    ``SummonDefinition.blocks_routes``, with paths recomputed around the tile
    and a bundle refused outright when the obstacles leave a spawn no way
    through.  Until now nothing set the flag, so both were compiled as ordinary
    devices that enemies would simply walk past.
    """

    if not skill_table:
        return False
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            if str(level.get("name") or "") == ROUTE_OBSTACLE_SKILL_NAME:
                return True
    return False


#: PRTS-only radii for device auras whose skill omits one, in milli-tiles.
#: 汲营枯枝: PRTS 「汲营枯枝」 -- 「1.1半径范围内的我方单位每秒受到 50/25 点
#: 真实普通伤害」.  The 50 and 25 are shipped; only the radius is not, which is
#: the same shape as the `boom.*` radius table.
DEVICE_POLLUTION_RADIUS_MILLI_TILES = {"trap_086_larva": 1100}


def _token_pollution_aura(
    character_key: str,
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
) -> tuple[int, int, int]:
    """``(radius_milli, low_damage, high_damage)`` for a device's pollution aura.

    汲营枯枝 ships the two damage figures in `sktok_larva` and no radius, so
    the radius comes from the cited table and a carrier absent from it reads as
    no aura rather than a guessed reach -- the same fail-closed rule the death
    explosion follows.

    `low`/`high` are **ground heights**, not a damage range: the shipped
    `PollutedZoneState` already calls the same two names `low_ground_damage` and
    `high_ground_damage`, and PRTS states them as 50 and 25 for low and high.
    """

    if not skill_table:
        return 0, 0, 0
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            values = {
                str(item.get("key")): item.get("value")
                for item in (level.get("blackboard") or [])
            }
            low = values.get("polluted_damage_low")
            high = values.get("polluted_damage_high")
            if not isinstance(low, (int, float)) or low <= 0:
                continue
            if not isinstance(high, (int, float)) or high <= 0:
                continue
            radius = DEVICE_POLLUTION_RADIUS_MILLI_TILES.get(character_key, 0)
            if not radius:
                # Fail closed: a reach that is not in the data and not in the
                # cited table is not a reach to guess at.
                return 0, 0, 0
            return radius, int(round(float(low))), int(round(float(high)))
    return 0, 0, 0


#: `x-1`, frozen from `range_table.json`: a 13-tile diamond reaching two tiles
#: along each axis and one along the diagonals.  Devices carry their reach as a
#: `rangeId` into that table, and no radius reproduces this shape, so the grid
#: travels as data rather than as a number.
PULSE_RANGE_X1 = (
    (0, -2),
    (-1, -1),
    (0, -1),
    (1, -1),
    (-2, 0),
    (-1, 0),
    (0, 0),
    (1, 0),
    (2, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
    (0, 2),
)

#: The reach the pulse reader accepts.  A device whose skill points at some
#: other grid would need that grid frozen too, and silently reusing this one
#: would put the wrong tiles under it.
_PULSE_RANGE_IDS = {"x-1": PULSE_RANGE_X1}





#: 「向**部署方向**持续发出重整束流」 / 「**改变**重整束流的**流向**」.
_BEAM_EMITTER_PHRASE = "持续发出"
_BEAM_REDIRECT_PHRASE = "改变"



#: 「**反弹**部分受到的伤害」 -- 尖刺宝箱's skill text.  Its sibling chests carry
#: the same shape of skill (「击破额外获得N源石锭」) with no such clause, so this
#: word is what separates the one with spikes from the two without.
_REFLECT_PHRASE = "反弹"


def _token_damage_reflect(
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """尖刺宝箱's reflected fraction, or an empty mapping.

    `trap_109_smrbox`「尖刺宝箱」 stands **72 times** across IS3 -- the same three
    chests appear on 36 of the 63 stages -- and its skill `sktok_smrbox`
    「刺人珍宝」 reads 「**反弹部分受到的伤害**，击破额外获得4源石锭」.

    「部分」 is the talent's `atk_scale` **0.4**.  The two halves are in different
    tables and neither is enough on its own: the skill says *that* it reflects,
    the talent says *how much*, and the talent has no name or description at all.
    """

    if not skill_table:
        return {}
    reflects = False
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            if _REFLECT_PHRASE in str(level.get("description") or ""):
                reflects = True
    if not reflects:
        return {}
    for talent in record.get("talents") or []:
        for candidate in talent.get("candidates") or []:
            for row in candidate.get("blackboard") or []:
                if str(row.get("key")) != "atk_scale":
                    continue
                value = row.get("value")
                if isinstance(value, (int, float)) and value > 0:
                    return {"damage_reflect_micros": round(float(value) * 1_000_000)}
    return {}


def _token_beam(record: Mapping[str, Any]) -> dict[str, Any]:
    """Which half of 重整束流 this device is, if either.

    Both `trap_062_magicstart`「“低音号”」 and `trap_063_magicturn`「L-7 “回音壁”」
    (IS3 **5-1**) carry **no figures at all** -- no talent blackboard, no skill.
    Their description is the whole specification, so it is what is read.
    """

    description = str(record.get("description") or "")
    if "重整束流" not in description:
        return {}
    if _BEAM_EMITTER_PHRASE in description:
        return {"beam_emitter": True}
    if _BEAM_REDIRECT_PHRASE in description and "流向" in description:
        return {"beam_redirector": True}
    return {}


def _token_frost_pulse(
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
    tps: int,
) -> dict[str, Any]:
    """源石冰晶's 【寒潮】, or an empty mapping.

    `trap_010_frosts`「源石冰晶」 stands **twice** in IS3 **4-8**, at (col 3,
    row 5) and (col 5, row 3), and its skill `sktok_frosts`「寒潮」 reads:

        「发射一道寒霜冲击波，使**周围我方单位**陷入**寒冷**状态 **{freeze}** 秒」

    with `freeze` **10.0**, `spCost` **15**, `initSp` **0**, `spType` 1.  So the
    cadence is derived the same way the shrine's is -- fifteen SP at one a
    second -- and the reach is the same 13-tile `x-1` grid.

    What it fires is not damage: 「寒冷」 is a **state**, and two of them make a
    freeze.  A defender who parks on the wrong tile in 4-8 is cold for ten
    seconds out of every fifteen.
    """

    if not skill_table:
        return {}
    phases = record.get("phases") or []
    if not phases:
        return {}
    frames = phases[-1].get("attributesKeyFrames") or []
    data = (frames[-1].get("data") or {}) if frames else {}
    sp_per_second = float(data.get("spRecoveryPerSec") or 0.0)
    offsets = _PULSE_RANGE_IDS.get(str(phases[-1].get("rangeId") or ""))
    if sp_per_second <= 0 or offsets is None:
        return {}
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            description = str(level.get("description") or "")
            if "寒冷" not in description:
                continue
            sp_data = level.get("spData") or {}
            sp_cost = sp_data.get("spCost")
            if not isinstance(sp_cost, (int, float)) or sp_cost <= 0:
                continue
            freeze = None
            for item in level.get("blackboard") or []:
                if str(item.get("key")) == "freeze":
                    freeze = item.get("value")
            if not isinstance(freeze, (int, float)) or freeze <= 0:
                continue
            return {
                "cold_pulse_ticks": max(1, round(float(freeze) * tps)),
                "cold_pulse_interval_ticks": max(
                    1, round(float(sp_cost) / sp_per_second * tps)
                ),
                "cold_pulse_range_offsets": offsets,
            }
    return {}


def _token_activation(
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """【研究成功】's 「技力满时启动」 threshold, or an empty mapping.

    `trap_075_bgarmn`「R-31重型动力装甲」 and `trap_077_rmtarmn`「R-11突击动力装甲」
    (IS3 **4-2**, four and one of them) carry `sktok_bgarmn_1` / `sktok_rmtarmn_1`:

        「**吸收范围内的实验产物回复技力，技力满时启动**」

    with `spCost` **100** and `initSp` **0**.  The description is the read: a
    device that charges from something it absorbs and then turns on is a
    different thing from one that fires a skill on a timer, and only the text
    says which this is.
    """

    if not skill_table:
        return {}
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            description = str(level.get("description") or "")
            if "技力满时启动" not in description:
                continue
            sp_cost = (level.get("spData") or {}).get("spCost")
            if isinstance(sp_cost, (int, float)) and sp_cost > 0:
                return {"activation_sp_cost": int(sp_cost)}
    return {}


def _token_pulse(
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
    tps: int,
) -> dict[str, Any]:
    """源石祭坛's 脉冲波, or an empty mapping.

    `sktok_ore` ships the payload (`value` 500) and the charge (`spCost` 7,
    `initSp` 0, `spType` 1 = auto-recovering); the **cadence** comes from the
    attribute frame's `spRecoveryPerSec` 1.0, so seven seconds is a product of
    two tables rather than a shipped period.

    The description is what says who it hits -- 「对周围所有**我方与敌方**单位造成
    500**真实伤害**」 -- and PRTS 「源石祭坛」 adds 「※造成预计算无途径真实无类型
    伤害，不会对**免疫脉冲波**的单位造成伤害」 and 「可对空」.  Nothing here reads
    the description for those: the both-sides rule is the mechanism itself and
    lives in the engine, and the immunity lives on the enemies that declare it.
    """

    if not skill_table:
        return {}
    phases = record.get("phases") or []
    if not phases:
        return {}
    frames = phases[-1].get("attributesKeyFrames") or []
    data = (frames[-1].get("data") or {}) if frames else {}
    sp_per_second = float(data.get("spRecoveryPerSec") or 0.0)
    offsets = _PULSE_RANGE_IDS.get(str(phases[-1].get("rangeId") or ""))
    if sp_per_second <= 0 or offsets is None:
        return {}
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            description = str(level.get("description") or "")
            if "真实伤害" not in description or "我方与敌方" not in description:
                continue
            sp_data = level.get("spData") or {}
            sp_cost = sp_data.get("spCost")
            if not isinstance(sp_cost, (int, float)) or sp_cost <= 0:
                continue
            damage = None
            for item in level.get("blackboard") or []:
                if str(item.get("key")) == "value":
                    value = item.get("value")
                    if isinstance(value, (int, float)):
                        damage = int(value)
            if not damage:
                continue
            seconds = float(sp_cost) / sp_per_second
            interval = round(seconds * tps)
            if interval <= 0:
                continue
            return {
                "pulse_damage": damage,
                "pulse_interval_ticks": interval,
                "pulse_range_offsets": offsets,
            }
    return {}


#: 「复仇黑手」的技能正文, 用来认出这台装置手里拿的是血债账款。
_BLOOD_DEBT_PHRASE = "触发复仇状态"


def _token_blood_debt(
    record: Mapping[str, Any],
    skill_table: Mapping[str, Any] | None,
    tps: int,
) -> dict[str, Any]:
    """「复仇黑手」的血债账款, 或者一个空映射。

    `sktok_vgctrl`「复仇黑手」「触发复仇状态」出货 `spCost` **500**、`initSp`
    **0**、`increment` **3.0**、`duration` **35.0**, 黑板 `atk` **0.2** /
    `def` **0.15** / `max_hp` **0.0**。

    积累速率与源石祭坛的脉冲周期同一种取法: **两张表的乘积**——属性帧的
    `spRecoveryPerSec` **1.0** 乘技能的 `increment` **3.0** 得 3/秒, 正是 PRTS
    说的「每秒自然积累速度为 3/秒」「从零开始积累需约 166.7 秒」(500÷3)。
    """

    if not skill_table:
        return {}
    phases = record.get("phases") or []
    if not phases:
        return {}
    frames = phases[-1].get("attributesKeyFrames") or []
    data = (frames[-1].get("data") or {}) if frames else {}
    sp_per_second = float(data.get("spRecoveryPerSec") or 0.0)
    if sp_per_second <= 0:
        return {}
    for entry in record.get("skills") or []:
        skill = skill_table.get(str(entry.get("skillId") or ""))
        if not skill:
            continue
        for level in skill.get("levels") or []:
            if _BLOOD_DEBT_PHRASE not in str(level.get("description") or ""):
                continue
            sp_data = level.get("spData") or {}
            sp_cost = sp_data.get("spCost")
            increment = sp_data.get("increment")
            duration = level.get("duration")
            if not isinstance(sp_cost, (int, float)) or sp_cost <= 0:
                continue
            if not isinstance(increment, (int, float)) or increment <= 0:
                continue
            if not isinstance(duration, (int, float)) or duration <= 0:
                continue
            blackboard = {
                str(item.get("key")): item.get("value")
                for item in (level.get("blackboard") or [])
                if isinstance(item, dict)
            }
            per_tick = round(sp_per_second * float(increment) * 1000 / tps)
            reckoning = round(float(duration) * tps)
            if per_tick <= 0 or reckoning <= 0:
                continue

            def _micros(key: str) -> int:
                value = blackboard.get(key)
                return round(float(value) * 1_000_000) if isinstance(
                    value, (int, float)
                ) else 0

            return {
                "blood_debt_ledger": BloodDebtLedger(
                    max_charge_milli=round(float(sp_cost) * 1000),
                    initial_charge_milli=round(
                        float(sp_data.get("initSp") or 0) * 1000
                    ),
                    charge_per_tick_milli=per_tick,
                    reckoning_ticks=reckoning,
                    reckoning_attack_bonus_micros=_micros("atk"),
                    reckoning_defense_bonus_micros=_micros("def"),
                    reckoning_max_hp_bonus_micros=_micros("max_hp"),
                )
            }
    return {}


def _token_summon(
    character_key: str,
    record: Mapping[str, Any],
    tps: int,
    skill_table: Mapping[str, Any] | None = None,
) -> SummonDefinition | None:
    """Build a summon contract for one device from `character_table`."""

    phases = record.get("phases") or []
    if not phases:
        return None
    frames = phases[-1].get("attributesKeyFrames") or []
    if not frames:
        return None
    data = frames[-1].get("data") or {}
    max_hp = int(data.get("maxHp") or 0)
    if max_hp <= 0:
        return None
    attack = max(0, int(data.get("atk") or 0))
    base_attack_time = float(data.get("baseAttackTime") or 0)
    attack_speed = float(data.get("attackSpeed") or 100.0) or 100.0
    if base_attack_time > 0:
        interval_ticks = max(1, round(base_attack_time * 100.0 / attack_speed * tps))
    else:
        interval_ticks = tps
    description = record.get("description") or ""
    life_point_cost, max_cost_gain, cost_gain = _token_death_effects(record)
    aura_points, aura_multiple = _token_enemy_aura(record)
    airflow = _token_airflow(record, skill_table)
    aura_radius, aura_low, aura_high = _token_pollution_aura(
        character_key, record, skill_table
    )
    blocks_routes = _token_blocks_routes(record, skill_table)
    pulse = _token_pulse(record, skill_table, tps)
    activation = _token_activation(record, skill_table)
    frost = _token_frost_pulse(record, skill_table, tps)
    beam = _token_beam(record)
    reflect = _token_damage_reflect(record, skill_table)
    blood_debt = _token_blood_debt(record, skill_table, tps)
    return SummonDefinition(
        summon_id=character_key,
        max_hp=max_hp,
        attack=attack,
        defense=max(0, int(data.get("def") or 0)),
        magic_resistance=min(100, max(0, int(float(data.get("magicResistance") or 0)))),
        attack_interval_ticks=interval_ticks,
        # `rangeId` indexes a grid in another table; 0 for a unit that cannot
        # attack, 1 otherwise, and the exact grid stays pending.
        attack_range_tiles=1 if attack else 0,
        # A route obstacle does not also block in melee.  障碍物 / 碎石 ship
        # `blockCnt: 3`, but the engine's obstacle model reroutes enemies
        # *around* the tile, so nothing ever arrives to be held -- carrying both
        # would be two contradictory models of one tile, which the contract
        # refuses outright.  The shipped 「如果障碍阻挡了敌人，敌人会破坏此障碍」
        # case is what the engine turns into a refusal when the obstacles leave
        # a spawn no path at all; the enemy-breaks-through behaviour itself is
        # not modelled.
        block_count=0 if blocks_routes else max(0, int(data.get("blockCnt") or 0)),
        blocks_routes=blocks_routes,
        # 盾卫: 「无法被友方角色治疗」 -- read from the shipped description
        # rather than an id allow-list, so a second such device is covered.
        receives_external_healing=not ("无法" in description and "治疗" in description),
        taunt_level=_token_taunt_level(record, skill_table),
        **airflow,
        pollution_aura_radius_milli_tiles=aura_radius,
        pollution_aura_low_damage=aura_low,
        pollution_aura_high_damage=aura_high,
        enemy_attack_speed_bonus=aura_points,
        enemy_move_speed_multiple_milli=aura_multiple,
        death_life_point_cost=life_point_cost,
        death_max_cost_gain=max_cost_gain,
        death_cost_gain=cost_gain,
        **pulse,
        **activation,
        **frost,
        **beam,
        **reflect,
        **blood_debt,
    )
