"""Source-keyed runtime contracts for executable enemy coefficient families."""

from __future__ import annotations

import re

from .models import ContentBundle, EnemyDefinition

# Re-exported so every existing ``from .enemy_coefficients import X`` works.
from .coefficient_provenance import (  # noqa: F401
    authorize_family_outside_frozen_stage,  # noqa: F401,E402
    PATTERNED_COEFFICIENT_PATTERNS,
    RUSH_ACCELERATION_PATTERN,
    RUSH_PATTERNS,
    RUSH_PREDELAY_PATTERN,
    RUSH_REQUIRED_LEAVES,
    _authorize_family_rows,
    coefficient_value,
    patterned_coefficient_leaf,
    rush_acceleration_rows,
)
from .is3_talent_families import (  # noqa: F401,E402
    RUSH_CONTACT_COEFFICIENTS,
    CRASH_CONTACT_KEYS,
    CRASH_CONTACT_RADIUS_MILLI_TILES,
    RUSH_CONTACT_KEYS,
    RUSH_CONTACT_RADIUS_MILLI_TILES,
    IDLE_SPEED_COEFFICIENTS,
    IDLE_SPEED_KEYS,
    EMBER_REVIVE_COEFFICIENTS,
    EMBER_REVIVE_KEYS,
    EMBER_TRANSITION_SECONDS,
    REDUCE_BLOCK_KEYS,
    POLLUTION_AURA_COEFFICIENTS,
    POLLUTION_AURA_KEYS,
    ATTACK_STACK_COEFFICIENTS,
    ATTACK_STACK_KEYS,
    attack_stack_rows,
    frozen_target_attack_scale,
    ember_revive_rows,
    pollution_aura_rows,
    reduce_block_rows,
    idle_speed_rows,
    death_unload_count,
    death_unload_enemy_id,
    rush_contact_rows,
    validate_attack_stack_coefficients,
    validate_splash_coefficients,
    validate_ember_revive_coefficients,
    validate_pollution_aura_coefficients,
    validate_idle_speed_coefficients,
    validate_crash_contact_coefficients,
    validate_rush_contact_coefficients,
    ATTACK_CONTROL_COEFFICIENTS,
    ATTACK_CONTROL_KEYS,
    ATTACK_SPEED_AURA_COEFFICIENTS,
    ATTACK_SPEED_AURA_KEYS,
    ATTACK_SPEED_FRACTION_TO_POINTS,
    AURA_DEFUP_RADIUS_MILLI_TILES,
    BOOM_COEFFICIENTS,
    BOOM_FUSE_TICKS,
    BOOM_FUSE_TICKS_BY_ENEMY,
    BOOM_KEYS,
    DEATH_ENERGY_KEYS,
    BOOM_RADIUS_MILLI_TILES_BY_ENEMY,
    COLD_ATTACK_SPEED_POINTS,
    DEATH_TRANSFORM_BLACKBOARD_KEY,
    DEFENCE_AURA_COEFFICIENTS,
    DEFENCE_AURA_KEYS,
    ELEMENTAL_BURST_ARTS_DAMAGE,
    ELEMENTAL_BURST_COOLDOWN_TICKS,
    ELEMENTAL_BURST_PHYSICAL_DAMAGE,
    ELEMENTAL_BURST_STUN_SECONDS,
    ELEMENTAL_BURST_TRUE_DAMAGE,
    ELEMENTAL_GAUGE_MAX,
    ELEMENTAL_PERMANENT_DEFENCE_LOSS,
    ELEMENTAL_RATIO_COEFFICIENTS,
    ELEMENTAL_RATIO_KEYS,
    ENERGY_COEFFICIENTS,
    elemental_damage_type,
    validate_elemental_ratio_coefficients,
    RANGED_MODE_COEFFICIENTS,
    RANGED_MODE_KEYS,
    RANGED_MODE_TARGETS_BY_ENEMY,
    validate_ranged_mode_coefficients,
    ENERGY_KEYS,
    ENERGY_CHARGE_PER_SECOND,
    ENERGY_CHARGE_PHRASES,
    STEAL_ENERGY_COEFFICIENTS,
    STEAL_ENERGY_KEYS,
    enemy_is_stealthed,
    is_energy_charger,
    pulse_empowered_damage_type,
    pulse_immune,
    ranged_while_unblocked,
    unblocked_physical_evade,
    validate_energy_coefficients,
    validate_steal_energy_coefficients,
    DEF_REDUCE_COEFFICIENTS,
    DEF_REDUCE_KEYS,
    CHARGE_BURST_COEFFICIENTS,
    CHARGE_BURST_KEYS,
    QUILL_SPRAY_KEYS,
    quill_spray_rows,
    FREEZE_AURA_COEFFICIENTS,
    FREEZE_AURA_KEYS,
    FROZEN_MAGIC_RESISTANCE_PENALTY,
    FROZEN_TARGET_DAMAGE_COEFFICIENTS,
    FROZEN_TARGET_DAMAGE_KEYS,
    INCOMING_DAMAGE_SCALE_COEFFICIENTS,
    INCOMING_DAMAGE_SCALE_KEYS,
    INVULNERABILITY_COEFFICIENTS,
    INVULNERABILITY_KEYS,
    LOW_HP_ATTACK_KEYS,
    MULTI_TARGET_COEFFICIENTS,
    MULTI_TARGET_KEYS,
    PERIODIC_SELF_DAMAGE_COEFFICIENTS,
    PERIODIC_SELF_DAMAGE_KEYS,
    SELF_DECAY_RATIO_KEYS,
    RAGE_COEFFICIENTS,
    RAGE_KEYS,
    REAR_HIT_RESISTANCE_COEFFICIENTS,
    REAR_HIT_RESISTANCE_KEYS,
    REFRACTING_COEFFICIENTS,
    REFRACTING_KEYS,
    TAUNT_LEVEL_COEFFICIENTS,
    TAUNT_LEVEL_KEYS,
    TRAIT_ABILITY_BLADE_KEYS,
    TRAIT_ABILITY_COEFFICIENTS,
    TRAIT_ABILITY_GUARD_KEYS,
    TRAIT_ABILITY_KEYS,
    TRAIT_ABILITY_PAIRING_RADIUS_MILLI_TILES,
    VAMPIRE_COEFFICIENTS,
    VAMPIRE_KEYS,
    WEAKNESS_FRONT_PROTECTED_PHRASE,
    WEAKNESS_REAR_PROTECTED_PHRASE,
    BLACK_FOG_RANGE_OFFSETS,
    black_fog_range_offsets,
    death_transform_enemy_id,
    hologram_enemy_id,
    validate_attack_control_coefficients,
    validate_attack_speed_aura_coefficients,
    validate_boom_coefficients,
    validate_death_energy_coefficients,
    validate_death_transform,
    validate_def_reduce_coefficients,
    validate_defence_aura_coefficients,
    validate_charge_burst_coefficients,
    validate_quill_spray_coefficients,
    validate_freeze_aura_coefficients,
    validate_frozen_target_damage_coefficients,
    validate_incoming_damage_scale_coefficients,
    validate_invulnerability_coefficients,
    validate_multi_target_coefficients,
    validate_periodic_self_damage_coefficients,
    validate_self_decay_ratio_coefficients,
    validate_rage_coefficients,
    validate_rear_hit_resistance_coefficients,
    validate_refracting_coefficients,
    validate_rush_acceleration_coefficients,
    validate_taunt_level_coefficients,
    validate_trait_ability_coefficients,
    validate_vampire_coefficients,
    weakness_protected_side_from_description,
)


WAITING_FLOCK_BUNDLE_ID = "level_rogue3_1-4"
WAITING_FLOCK_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/level_rogue3_1-4.json"
)
WAITING_FLOCK_COEFFICIENTS: dict[str, dict[str, int]] = {
    "enemy_1106_byokai_b": {
        "buff.duration": 60_000_000,
        "buff.attack_speed": 100_000_000,
        "buff.def": 500_000,
    },
    "enemy_2001_duckmi": {"run.attack@move_speed": 4_000_000},
    "enemy_2034_sythef": {"Combat.attack@stun": 4_000_000},
    "enemy_2043_smsbr": {
        "SpeedUp.move_speed": 3_000_000,
        "SpeedUp.duration": 2_000_000,
        "SpeedUp.cooldown": 5_000_000,
    },
    "enemy_2062_smcar": {
        "Suicide.duration": 60_000_000,
        "HitEnemy.atk_scale": 1_000_000,
        "HitEnemy.base_exp": 2_000_000,
    },
}
WAITING_FLOCK_KEYS = frozenset(
    key for coefficients in WAITING_FLOCK_COEFFICIENTS.values() for key in coefficients
)

# The Tiacauh family is one coherent blocked-combat state machine, not a set of
# independent allow-list exceptions:
#
# * ``Def.def`` is an additive defense bonus while the enemy is blocked;
# * the witch's blocked attack becomes physical and uses the source attack
#   multiplier instead of its ordinary ranged Arts packet; and
# * each ripper attack adds the source flat defense reduction to its target.
#
# Values and source pointers are frozen from enemy_database.json.  A stage may
# use any source-exact subset of these variants, but it cannot reuse the keys on
# another enemy or silently change a coefficient.
TIACAUH_BLOCKED_COMBAT_COEFFICIENTS: dict[str, dict[str, int]] = {
    "enemy_1093_ccsbr": {"Def.def": 130_000_000},
    "enemy_1093_ccsbr_2": {"Def.def": 180_000_000},
    "enemy_1095_ccripr": {"defdown.def": -10_000_000},
    "enemy_1095_ccripr_2": {"defdown.def": -15_000_000},
    "enemy_1096_ccwitch": {
        "Def.def": 200_000_000,
        "combat.attack@ccwitch_rage_attack.atk_scale": 4_000_000,
    },
    "enemy_1096_ccwitch_2": {
        "Def.def": 300_000_000,
        "combat.attack@ccwitch_rage_attack.atk_scale": 4_000_000,
    },
}
TIACAUH_BLOCKED_COMBAT_KEYS = frozenset(
    key
    for coefficients in TIACAUH_BLOCKED_COMBAT_COEFFICIENTS.values()
    for key in coefficients
)

# `revive.unmove_duration` is deliberately **absent** from these four rows.
# The shipped table gives it **per variant** -- stages 1-2 and b-5-b use a
# variant whose value is 85 s, others 150 s -- so no single frozen number can be
# right, and pinning 150 s refused those two real stages outright.  A matrix
# pins the rows it names; a row it does not name is authorized on provenance
# instead.  `hp_ratio` and `move_speed` do not vary and stay pinned.
STANDBY_REVIVE_COEFFICIENTS: dict[str, dict[str, int]] = {
    "enemy_1074_dbskar": {
        "revive.hp_ratio": 999_000,
        "revive.move_speed": 2_000_000,
    },
    "enemy_1074_dbskar_2": {
        "revive.hp_ratio": 999_000,
        "revive.move_speed": 2_000_000,
    },
    "enemy_1075_dmgswd": {
        "revive.hp_ratio": 999_000,
        "revive.move_speed": 2_000_000,
    },
    "enemy_1075_dmgswd_2": {
        "revive.hp_ratio": 999_000,
        "revive.move_speed": 2_000_000,
    },
    "enemy_1101_plkght": {
        "buff.duration": 30_000_000,
        "buff.def": 3_000_000_000,
        "buff.magic_resistance": 95_000_000,
    },
    "enemy_1101_plkght_2": {
        "buff.duration": 30_000_000,
        "buff.def": 3_000_000_000,
        "buff.magic_resistance": 95_000_000,
    },
    "enemy_1101_plkght_3": {
        "buff.duration": 60_000_000,
        "buff.def": 3_000_000_000,
        "buff.magic_resistance": 95_000_000,
    },
}
# Family **membership** and **pinned values** are two different things, and
# deriving the first from the second coupled them: dropping the per-variant
# `revive.unmove_duration` from the matrix silently removed it from the
# allow-list too, so a real key became "non-allowlisted".  The set is therefore
# stated, with the per-variant key added back explicitly.
STANDBY_REVIVE_KEYS = frozenset(
    key
    for coefficients in STANDBY_REVIVE_COEFFICIENTS.values()
    for key in coefficients
) | {"revive.unmove_duration"}
STANDBY_REVIVE_EXCLUSIVE_KEYS = STANDBY_REVIVE_KEYS - WAITING_FLOCK_KEYS
STANDBY_REVIVE_ENEMY_IDS = frozenset(STANDBY_REVIVE_COEFFICIENTS)
STANDBY_REVIVE_RANGED_MULTI_TARGET_IDS = frozenset(
    {"enemy_1074_dbskar", "enemy_1074_dbskar_2"}
)
STANDBY_REVIVE_MELEE_ARTS_IDS = frozenset(
    {"enemy_1075_dmgswd", "enemy_1075_dmgswd_2"}
)
STANDBY_REVIVE_INITIAL_GUARD_IDS = frozenset(
    {"enemy_1101_plkght", "enemy_1101_plkght_2", "enemy_1101_plkght_3"}
)

# Redmark infiltrators and their holograms are one source-closed lifecycle:
# the first positive damage packet creates the identity-bound hologram, the
# infiltrator is unblockable and one taunt tier lower while that child is
# alive, and the hologram itself never moves.  ``createGhost.enemy_key`` is a
# string blackboard value and therefore is not present in the numeric
# ``mechanic_coefficients`` projection; keep that exact identity edge here
# instead of guessing it from enemy-name prefixes.
GHOST_HOLOGRAM_BUNDLE_ID = "level_rogue3_2-3"
GHOST_HOLOGRAM_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/"
    "level_rogue3_2-3.json"
)
GHOST_HOLOGRAM_COEFFICIENTS: dict[str, dict[str, int]] = {
    "enemy_1136_redace": {
        "createGhost.taunt_level": -1_000_000,
        "storm.prob": 400_000,
    },
    "enemy_1136_redace_2": {
        "createGhost.taunt_level": -1_000_000,
        "storm.prob": 400_000,
    },
    "enemy_1139_hologc": {"storm.prob": 400_000},
    "enemy_1139_hologc_2": {"storm.prob": 400_000},
}
GHOST_HOLOGRAM_KEYS = frozenset(
    key
    for coefficients in GHOST_HOLOGRAM_COEFFICIENTS.values()
    for key in coefficients
)
GHOST_REDACE_TO_HOLOGRAM = {
    "enemy_1136_redace": "enemy_1139_hologc",
    "enemy_1136_redace_2": "enemy_1139_hologc_2",
}
GHOST_REDACE_IDS = frozenset(GHOST_REDACE_TO_HOLOGRAM)
GHOST_HOLOGRAM_IDS = frozenset(GHOST_REDACE_TO_HOLOGRAM.values())

# Blade Point contains two independent but source-bound enemy mechanics.  The
# Empire Vanguard Elite attacks at range only while unblocked and doubles its
# attack below half health.  The "Devil's Blade" continuously projects the
# source ``x-4`` (3x3) Kingdom around its current position: friendlies inside
# it have -50 ASPD and the blade's attacks against them use 250% ATK as
# physical damage.  ``BlackFog.range_id`` is a string blackboard value and is
# therefore absent from the numeric package projection; its exact range is
# frozen here from range_table.json rather than inferred from the two numeric
# rows.
BLADE_POINT_BUNDLE_ID = "level_rogue3_b-1"
BLADE_POINT_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/"
    "level_rogue3_b-1.json"
)
BLADE_POINT_COEFFICIENTS: dict[str, dict[str, int]] = {
    "enemy_1113_empace": {"atkup.atk": 1_000_000},
    "enemy_2048_smgrd": {
        "BlackFog.attack_speed": -50_000_000,
        "DamageUp.atk_scale": 2_500_000,
    },
}
BLADE_POINT_KEYS = frozenset(
    key
    for coefficients in BLADE_POINT_COEFFICIENTS.values()
    for key in coefficients
)

# Mainline 12's disguised civilians, scouts, and first-splash brute are one
# stage-bound identity lifecycle.  The numeric rows alone are insufficient:
# source prefab semantics make the actors invincible until two detection
# phases complete, attach the scout's revealed-mode payload, restore the
# civilian's maximum deployment cost only after confirmed retreat, and replace
# the brute's first ordinary attack with a physical x-5 cross packet.
MAINLINE12_BUNDLE_ID = "level_rogue3_2-4"
MAINLINE12_CONTENT_VERSION = "v17"
MAINLINE12_STAGE_ID = "ro3_n_2_4"
MAINLINE12_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/"
    "level_rogue3_2-4.json"
)
MAINLINE12_PACKAGE_HASH = (
    "c4d11553fabb4fa02ca8e055f43821ccfab23147f26f95dcda1296d273dc8ae1"
)
MAINLINE12_ENEMY_DATABASE_SHA256 = (
    "8a3b26e3c890f140b7edc0b44ed870b26098646a5682cb21cfee7e554e005d14"
)
MAINLINE12_COEFFICIENTS: dict[str, dict[str, tuple[int, str]]] = {
    "enemy_1313_wdfmr": {
        "civilianTalent.max_cost": (
            2_000_000,
            "enemy_database.json#/enemy_1313_wdfmr/talentBlackboard/0/value",
        ),
    },
    "enemy_1314_wdnjd": {
        "EnhancedAttack.atk_scale": (
            2_000_000,
            "enemy_database.json#/enemy_1314_wdnjd/talentBlackboard/0/value",
        ),
        "moveSpeed.move_speed": (
            50_000,
            "enemy_database.json#/enemy_1314_wdnjd/talentBlackboard/1/value",
        ),
    },
    "enemy_1315_wdyjd": {
        "moveSpeed.move_speed": (
            200_000,
            "enemy_database.json#/enemy_1315_wdyjd/talentBlackboard/0/value",
        ),
    },
    "enemy_1320_wdrrl": {
        "minimum_block_count_to_stop": (
            3_000_000,
            "enemy_database.json#/enemy_1320_wdrrl/description",
        ),
        "AOEAttack.atk_scale": (
            1_500_000,
            "enemy_database.json#/enemy_1320_wdrrl/talentBlackboard/0/value",
        ),
    },
}
MAINLINE12_KEYS = frozenset(
    key for coefficients in MAINLINE12_COEFFICIENTS.values() for key in coefficients
)
MAINLINE12_IDENTITY_KEYS = MAINLINE12_KEYS - {"minimum_block_count_to_stop"}
MAINLINE12_DISGUISED_IDS = frozenset(
    {"enemy_1313_wdfmr", "enemy_1314_wdnjd", "enemy_1315_wdyjd"}
)
MAINLINE12_CIVILIAN_IDS = frozenset({"enemy_1313_wdfmr"})
MAINLINE12_ENHANCED_ATTACK_IDS = frozenset({"enemy_1314_wdnjd"})
MAINLINE12_REVEALED_INVISIBLE_IDS = frozenset({"enemy_1315_wdyjd"})
MAINLINE12_FIRST_AOE_IDS = frozenset({"enemy_1320_wdrrl"})
MAINLINE12_X5_RANGE_OFFSETS = frozenset(
    {(0, 0), (0, 1), (-1, 0), (1, 0), (0, -1)}
)
MAINLINE12_BRUTE_SPAWN_BINDINGS = {
    "w0-f2-a33": ("route-043", 2760, 33),
    "w0-f2-a74": ("route-084", 5340, 74),
}
MAINLINE12_DETECTION_INTERVAL_TICKS = 3
MAINLINE12_DETECTION_PHASE1_THRESHOLD = 300
MAINLINE12_DETECTION_PHASE1_BLOCKED_DELTA = 2
MAINLINE12_DETECTION_PHASE2_THRESHOLD = 300
MAINLINE12_DETECTION_PHASE2_BLOCKED_DELTA = 3
MAINLINE12_DETECTION_UNBLOCKED_DELTA = -1
MAINLINE12_SWITCH_TRIGGER_TICKS = 2

# Return Body's four previously non-allowlisted enemy rows form one combat
# family: death/attack pollution zones, the heal-cleared nonlethal bleed, the
# tank's attack-recovery pollution cycle, and the artillery barrage.  The
# numeric matrix is frozen from enemy_database.json while the event graph is
# pinned to the exported enemy prefab and buff-template catalogs.
RETURN_BODY_BUNDLE_ID = "level_rogue3_2-6"
RETURN_BODY_CONTENT_VERSION = "v17"
RETURN_BODY_STAGE_ID = "ro3_n_2_6"
RETURN_BODY_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/"
    "level_rogue3_2-6.json"
)
RETURN_BODY_PACKAGE_HASH = (
    "1c2115475b7672121182a02b34a75e12e1bd783bdfdcdfcfeceec3a7b6bf2577"
)
RETURN_BODY_ENEMY_DATABASE_SHA256 = MAINLINE12_ENEMY_DATABASE_SHA256
RETURN_BODY_PREFAB_CATALOG_SHA256 = (
    "ebe89ba77e15541b52bf117820ecf65838c840fee6e65d397ebc46c601c17ef8"
)
RETURN_BODY_BUFF_TEMPLATE_SHA256 = (
    "53acd2e9729dc7e8ab4c95d4324e592f0d12640987a06d3d1b1d89dfb2a8423b"
)
RETURN_BODY_COEFFICIENTS: dict[str, dict[str, tuple[int, str]]] = {
    "enemy_1267_nhpbr": {
        "PollutedDie.polluted_damage_high": (
            25_000_000,
            "enemy_database.json#/enemy_1267_nhpbr/PollutedDie.polluted_damage_high",
        ),
        "PollutedDie.polluted_damage_low": (
            50_000_000,
            "enemy_database.json#/enemy_1267_nhpbr/PollutedDie.polluted_damage_low",
        ),
        "PollutedDie.projectile_life_time": (
            8_000_000,
            "enemy_database.json#/enemy_1267_nhpbr/PollutedDie.projectile_life_time",
        ),
        "PollutedDie.projectile_range": (
            2_000_000,
            "enemy_database.json#/enemy_1267_nhpbr/PollutedDie.projectile_range",
        ),
    },
    "enemy_1270_nhstlk": {
        "Bleeding.attack@bleeding_damage": (
            100_000_000,
            "enemy_database.json#/enemy_1270_nhstlk/Bleeding.attack@bleeding_damage",
        ),
        "Bleeding.attack@duration": (
            10_000_000,
            "enemy_database.json#/enemy_1270_nhstlk/Bleeding.attack@duration",
        ),
    },
    "enemy_1272_nhtank": {
        "Empty.attack@chuang_atk_scale": (
            2_000_000,
            "enemy_database.json#/enemy_1272_nhtank/Empty.attack@chuang_atk_scale",
        ),
        "Empty.sp": (
            2_000_000,
            "enemy_database.json#/enemy_1272_nhtank/Empty.sp",
        ),
        "PollutedRangedAtk.polluted_damage_high": (
            25_000_000,
            "enemy_database.json#/enemy_1272_nhtank/PollutedRangedAtk.polluted_damage_high",
        ),
        "PollutedRangedAtk.polluted_damage_low": (
            50_000_000,
            "enemy_database.json#/enemy_1272_nhtank/PollutedRangedAtk.polluted_damage_low",
        ),
        "PollutedRangedAtk.projectile_delay_time": (
            10_000_000,
            "enemy_database.json#/enemy_1272_nhtank/PollutedRangedAtk.projectile_delay_time",
        ),
        "PollutedRangedAtk.projectile_life_time": (
            10_000_000,
            "enemy_database.json#/enemy_1272_nhtank/PollutedRangedAtk.projectile_life_time",
        ),
        "PollutedRangedAtk.range_radius": (
            2_200_000,
            "enemy_database.json#/enemy_1272_nhtank/PollutedRangedAtk.range_radius",
        ),
    },
    "enemy_1273_stmgun": {
        "Cannon.atk_scale": (
            550_000,
            "enemy_database.json#/enemy_1273_stmgun/Cannon.atk_scale",
        ),
        "Cannon.cooldown": (
            25_000_000,
            "enemy_database.json#/enemy_1273_stmgun/Cannon.cooldown",
        ),
        "Cannon.initCooldown": (
            10_000_000,
            "enemy_database.json#/enemy_1273_stmgun/Cannon.initCooldown",
        ),
    },
}
RETURN_BODY_KEYS = frozenset(
    key for coefficients in RETURN_BODY_COEFFICIENTS.values() for key in coefficients
)
RETURN_BODY_CANNON_IDS = frozenset({"enemy_1273_stmgun"})
RETURN_BODY_POLLUTION_PULSE_TICKS = 30
RETURN_BODY_BLEEDING_PULSE_TICKS = 30
RETURN_BODY_CANNON_BARRAGE_SHOTS = 10
RETURN_BODY_CANNON_WINDUP_TICKS = 45
RETURN_BODY_CANNON_SHOT_INTERVAL_TICKS = 15
RETURN_BODY_CANNON_RANGE_OFFSETS = frozenset(
    (col, row) for col in (-1, 0, 1) for row in (-1, 0, 1)
)




























































def is_runtime_coefficient_key(key: str) -> bool:
    """Whether the engine has a code path for this coefficient key.

    This is the single gate the whole chain hangs off: ``DERIVABLE_COEFFICIENT_KEYS``
    derives from it, the stage compiler only turns matching blackboard keys into
    ``mechanic_coefficients``, and everything else is reported as an
    unrepresented ``enemy_talent``.
    """

    return key in RUNTIME_COEFFICIENT_KEYS or patterned_coefficient_leaf(key) is not None




#: 「被击倒时，使『血债账款』增加」 -- 家族士兵 **20**, 家族恫吓者 **35**, 强暴恫吓者
#: **80**.
#:
#: **这里原来写着「血债账款是 run 层计数器, 这是战斗引擎, 所以这个数在这里不花」
#: ——错了。** 它花在场上: 「复仇黑手」`trap_092_vgctrl`(ev-5)手里拿着这本账,
#: 攒满 **500** 就进【清算时刻】, 35 秒内全场敌人 +20% 攻 / +15% 防。
#: **找不到消费方不等于没有消费方**——当时的证据是「引擎里没人读它」, 而那恰恰是
#: 待查的那件事本身。
BLOOD_DEBT_KEYS = frozenset(
    {
        "DeathRattle.sp",
        # 叙拉古平民 `enemy_3004_speople` (ev-5): 「死亡时会使『血债账款』**大幅**
        # 增加」 -- **40**, twice the family soldier's 20, under a different
        # prefix.  The counter is the same one, which is why it is read the same
        # way; the prefix says who is paying, not what is being paid.
        "Passive.speople_dead_vg.sp",
    }
)


#: 【清算时刻】里「部分敌人激活额外的能力」的那一份。
#:
#: 家族暗影灭迹人 `enemy_1283_sgkill_2`(ev-5)出货四个键: `Passive.atk` **0.15**、
#: `Passive.def` **0.15**、`Passive.attack_speed` **50**、`Passive.max_stack_cnt`
#: **5**。PRTS 天赋正文: 「『清算时刻』持续期间，自身攻击力+15%，防御力+15%，
#: 攻击速度+50」。
#:
#: **正文里没有任何「叠加」**, 而数据出货了 `max_stack_cnt` 5 —— 两者对不上, 所以
#: 这个键**登记而不实现**: 按 5 层去乘会把 +15% 变成 +75%, 那是发明。三个有正文的
#: 数照做, 第四个报出来。
RECKONING_KEYS = frozenset(
    {
        "Passive.atk",
        "Passive.def",
        "Passive.attack_speed",
        "Passive.max_stack_cnt",
    }
)

RUNTIME_COEFFICIENT_KEYS = (
    INCOMING_DAMAGE_SCALE_KEYS
    | BLOOD_DEBT_KEYS
    | RECKONING_KEYS
    | LOW_HP_ATTACK_KEYS
    | frozenset(ATTACK_CONTROL_KEYS)
    | INVULNERABILITY_KEYS
    | REAR_HIT_RESISTANCE_KEYS
    | FREEZE_AURA_KEYS
    | DEFENCE_AURA_KEYS
    | CHARGE_BURST_KEYS
    | ATTACK_SPEED_AURA_KEYS
    | FROZEN_TARGET_DAMAGE_KEYS
    | REFRACTING_KEYS
    | ENERGY_KEYS
    | RANGED_MODE_KEYS
    | ELEMENTAL_RATIO_KEYS
    | STEAL_ENERGY_KEYS
    | BOOM_KEYS
    | DEATH_ENERGY_KEYS
    | DEF_REDUCE_KEYS
    | PERIODIC_SELF_DAMAGE_KEYS
    | SELF_DECAY_RATIO_KEYS
    | VAMPIRE_KEYS
    | MULTI_TARGET_KEYS
    | TRAIT_ABILITY_KEYS
    | TAUNT_LEVEL_KEYS
    | RAGE_KEYS
    | RUSH_CONTACT_KEYS
    | CRASH_CONTACT_KEYS
    | QUILL_SPRAY_KEYS
    | IDLE_SPEED_KEYS
    | EMBER_REVIVE_KEYS
    | POLLUTION_AURA_KEYS
    | ATTACK_STACK_KEYS
    | WAITING_FLOCK_KEYS
    | TIACAUH_BLOCKED_COMBAT_KEYS
    | STANDBY_REVIVE_KEYS
    | GHOST_HOLOGRAM_KEYS
    | BLADE_POINT_KEYS
    | MAINLINE12_KEYS
    | RETURN_BODY_KEYS
)






def validate_waiting_flock_coefficients(content: ContentBundle) -> bool:
    """Authorize only the frozen waiting-flock coefficient matrix.

    The return value says whether this contract handled the bundle.  A partial
    or altered copy raises instead of silently falling back to generic combat.
    """

    present = {
        coefficient.key for enemy in content.enemies for coefficient in enemy.mechanic_coefficients
    }
    if not present or not present <= WAITING_FLOCK_KEYS:
        return False
    if content.bundle_id != WAITING_FLOCK_BUNDLE_ID or content.source != WAITING_FLOCK_SOURCE:
        raise ValueError("waiting-flock coefficient keys escaped their frozen stage binding")
    actual = {
        enemy.enemy_id: {
            coefficient.key: coefficient.value_micros for coefficient in enemy.mechanic_coefficients
        }
        for enemy in content.enemies
        if enemy.mechanic_coefficients
    }
    if actual != WAITING_FLOCK_COEFFICIENTS:
        raise ValueError("waiting-flock coefficient matrix differs from the frozen source")
    return True


def validate_tiacauh_blocked_combat_coefficients(content: ContentBundle) -> bool:
    """Validate every Tiacauh row against the frozen enemy-database matrix."""

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy,
            TIACAUH_BLOCKED_COMBAT_KEYS,
            TIACAUH_BLOCKED_COMBAT_COEFFICIENTS,
            "Tiacauh blocked-combat",
        ):
            handled = True
    return handled


def validate_standby_revive_coefficients(content: ContentBundle) -> bool:
    """Validate complete standby/wake and initial-guard rows by enemy identity."""

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy,
            STANDBY_REVIVE_KEYS,
            STANDBY_REVIVE_COEFFICIENTS,
            "standby/revive",
        ):
            handled = True
    return handled


def validate_ghost_hologram_coefficients(content: ContentBundle) -> bool:
    """Validate the complete first-hit hologram lifecycle and its stage context."""

    present = {
        coefficient.key
        for enemy in content.enemies
        for coefficient in enemy.mechanic_coefficients
    }
    if not present & GHOST_HOLOGRAM_KEYS:
        return False
    if content.bundle_id != GHOST_HOLOGRAM_BUNDLE_ID or content.source != GHOST_HOLOGRAM_SOURCE:
        active_storm_actions = sorted(
            {
                action.key
                for wave in content.source_waves
                for fragment in wave.fragments
                for action in fragment.actions
                if action.action_type == 6
                and action.key is not None
                and action.key.startswith("trap_036_storm")
            }
        )
        if active_storm_actions:
            raise ValueError(
                "active storm device action runtime is not represented: "
                f"{active_storm_actions}"
            )
        # Not the storm-free stage this family was frozen to.  The storm check
        # above still stands on its own; what falls through here is only the
        # "this is not *the* stage" refusal.
        return authorize_family_outside_frozen_stage(
            content, GHOST_HOLOGRAM_KEYS, "ghost/hologram"
        )

    definitions = {enemy.enemy_id: enemy for enemy in content.enemies}
    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            GHOST_HOLOGRAM_KEYS,
            GHOST_HOLOGRAM_COEFFICIENTS,
            "ghost/hologram",
        ):
            continue
        handled = True
        hologram_id = GHOST_REDACE_TO_HOLOGRAM.get(enemy.enemy_id)
        if hologram_id is not None and hologram_id not in definitions:
            raise ValueError("ghost/hologram source identity edge has no enemy definition")
    return handled


def validate_blade_point_coefficients(content: ContentBundle) -> bool:
    """Validate Blade Point's range/health/Kingdom family as one source unit."""

    definitions = {enemy.enemy_id: enemy for enemy in content.enemies}
    # Scope the stage lock to Blade Point's *own* enemies.  Keying it on the
    # mere presence of a key locked the whole family to one stage, so an
    # unrelated enemy carrying `atkup.atk` elsewhere in IS3 (enemy_1025_reveng_2,
    # enemy_2005_axetro) made that stage unrunnable.  The frozen guarantee for
    # Blade Point's own rows is unchanged.
    owned = {
        enemy_id for enemy_id in BLADE_POINT_COEFFICIENTS
        if enemy_id in definitions
        and any(
            coefficient.key in BLADE_POINT_KEYS
            for coefficient in definitions[enemy_id].mechanic_coefficients
        )
    }
    if not owned:
        return False
    if content.bundle_id != BLADE_POINT_BUNDLE_ID or content.source != BLADE_POINT_SOURCE:
        # Not the stage this family was frozen to: fall through to
        # provenance rather than refusing the stage outright.
        return authorize_family_outside_frozen_stage(
            content, BLADE_POINT_KEYS, "Blade Point"
        )

    for enemy_id, expected in BLADE_POINT_COEFFICIENTS.items():
        enemy = definitions.get(enemy_id)
        if enemy is None:
            raise ValueError("Blade Point source-bound enemy definition is missing")
        actual = {
            coefficient.key: (coefficient.value_micros, coefficient.source_pointer)
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in BLADE_POINT_KEYS
        }
        expected_rows = {
            key: (
                value_micros,
                f"enemy_database.json#/{enemy_id}/{key}",
            )
            for key, value_micros in expected.items()
        }
        if actual != expected_rows:
            raise ValueError("Blade Point coefficient differs from the frozen source")
    return True


def validate_mainline12_coefficients(content: ContentBundle) -> bool:
    """Validate the complete source-bound Mainline 12 identity family."""

    present = {
        coefficient.key
        for enemy in content.enemies
        for coefficient in enemy.mechanic_coefficients
    }
    if not present & MAINLINE12_IDENTITY_KEYS:
        return False
    identity = (
        content.content_version,
        content.stage_id,
        content.bundle_id,
        content.source,
        content.source_package_hash,
    )
    if identity != (
        MAINLINE12_CONTENT_VERSION,
        MAINLINE12_STAGE_ID,
        MAINLINE12_BUNDLE_ID,
        MAINLINE12_SOURCE,
        MAINLINE12_PACKAGE_HASH,
    ):
        # Not the stage this family was frozen to: fall through to provenance
        # rather than refusing the stage outright.
        return authorize_family_outside_frozen_stage(
            content, MAINLINE12_IDENTITY_KEYS, "Mainline 12"
        )

    definitions = {enemy.enemy_id: enemy for enemy in content.enemies}
    for enemy_id, expected in MAINLINE12_COEFFICIENTS.items():
        enemy = definitions.get(enemy_id)
        if enemy is None:
            raise ValueError(
                "Mainline 12 AOEAttack.atk_scale source-bound family definition is missing"
            )
        actual = {
            coefficient.key: (
                coefficient.value_micros,
                coefficient.source_pointer,
                coefficient.source_sha256,
            )
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in MAINLINE12_KEYS
        }
        expected_rows = {
            key: (value_micros, pointer, MAINLINE12_ENEMY_DATABASE_SHA256)
            for key, (value_micros, pointer) in expected.items()
        }
        if actual != expected_rows:
            raise ValueError(
                "Mainline 12 AOEAttack.atk_scale coefficient matrix differs from the frozen source"
            )
    if definitions["enemy_1320_wdrrl"].minimum_block_count_to_stop != 3:
        raise ValueError("Mainline 12 brute block constraint differs from the frozen source")
    if definitions["enemy_1313_wdfmr"].life_damage != 0:
        raise ValueError("Mainline 12 civilian exit life damage differs from the frozen source")
    brute_spawns = {
        spawn.spawn_id: (spawn.route_id, spawn.tick, spawn.source_action_index, spawn.count)
        for spawn in content.spawns
        if spawn.enemy_id == "enemy_1320_wdrrl"
    }
    expected_brute_spawns = {
        spawn_id: (route_id, tick, action_index, 1)
        for spawn_id, (route_id, tick, action_index) in (
            MAINLINE12_BRUTE_SPAWN_BINDINGS.items()
        )
    }
    if brute_spawns != expected_brute_spawns:
        raise ValueError(
            "Mainline 12 AOEAttack.atk_scale spawn binding differs from the frozen source"
        )
    return True


def validate_return_body_coefficients(content: ContentBundle) -> bool:
    """Validate Return Body's complete source-bound coefficient state machines."""

    present = {
        coefficient.key
        for enemy in content.enemies
        for coefficient in enemy.mechanic_coefficients
    }
    if not present & RETURN_BODY_KEYS:
        return False
    identity = (
        content.content_version,
        content.stage_id,
        content.bundle_id,
        content.source,
        content.source_package_hash,
    )
    if identity != (
        RETURN_BODY_CONTENT_VERSION,
        RETURN_BODY_STAGE_ID,
        RETURN_BODY_BUNDLE_ID,
        RETURN_BODY_SOURCE,
        RETURN_BODY_PACKAGE_HASH,
    ):
        # Not the stage this family was frozen to: fall through to
        # provenance rather than refusing the stage outright.
        return authorize_family_outside_frozen_stage(
            content, RETURN_BODY_KEYS, "Return Body"
        )

    definitions = {enemy.enemy_id: enemy for enemy in content.enemies}
    for enemy_id, expected in RETURN_BODY_COEFFICIENTS.items():
        enemy = definitions.get(enemy_id)
        if enemy is None:
            raise ValueError("Return Body source-bound enemy definition is missing")
        actual = {
            coefficient.key: (
                coefficient.value_micros,
                coefficient.source_pointer,
                coefficient.source_sha256,
            )
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in RETURN_BODY_KEYS
        }
        expected_rows = {
            key: (value_micros, pointer, RETURN_BODY_ENEMY_DATABASE_SHA256)
            for key, (value_micros, pointer) in expected.items()
        }
        if actual != expected_rows:
            raise ValueError("Return Body coefficient matrix differs from the frozen source")
    return True










































# Stays here rather than in is3_talent_families: it cross-checks the Blade
# Point frozen matrix, which lives in this module.
def validate_low_hp_attack_coefficients(content: ContentBundle) -> bool:
    """Authorize half-health attack-boost rows outside Blade Point on provenance.

    Blade Point keeps its own frozen matrix for its own enemies; every other
    enemy carrying `atkup.*` is authorized by source pointer, which is what
    lets the same talent work on a different enemy.
    """

    keys = LOW_HP_ATTACK_KEYS | {"atkup.atk"}
    handled = False
    for enemy in content.enemies:
        if enemy.enemy_id in BLADE_POINT_COEFFICIENTS:
            continue
        if _authorize_family_rows(
            enemy, keys, {}, "half-health attack boost"
        ):
            handled = True
    return handled



def validate_blood_debt_coefficients(content: "ContentBundle") -> bool:
    """Authorize 「血债账款」, requiring a whole, positive figure.

    The figure is spent on 「复仇黑手」's ledger when one stands on the map, and
    merely reported when none does -- the carriers appear in stages that have no
    such device (1-3 / 3-5 / t-3), so a missing ledger is not a fault.
    """

    handled = False
    for enemy in content.enemies:
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in BLOOD_DEBT_KEYS:
                continue
            if coefficient.value_micros <= 0 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "blood debt is a whole positive figure: %s carries %s"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
            handled = True
    return handled


def validate_reckoning_coefficients(content: "ContentBundle") -> bool:
    """Authorize 【清算时刻】's per-enemy extras.

    The two ratios are fractions of the enemy's own stat and the attack-speed
    figure is whole points, which is the same split the engine already uses
    everywhere else -- so the gate checks the *shape* rather than the value:
    a negative extra would be a debuff wearing a buff's key.
    """

    handled = False
    for enemy in content.enemies:
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in RECKONING_KEYS:
                continue
            if coefficient.value_micros <= 0:
                raise ValueError(
                    "a reckoning extra must be positive: %s carries %s on %s"
                    % (enemy.enemy_id, coefficient.value_micros, coefficient.key)
                )
            if coefficient.key in ("Passive.attack_speed", "Passive.max_stack_cnt"):
                if coefficient.value_micros % 1_000_000:
                    raise ValueError(
                        "%s is whole points: %s carries %s"
                        % (coefficient.key, enemy.enemy_id, coefficient.value_micros)
                    )
            handled = True
    return handled


def validate_runtime_coefficients(content: ContentBundle) -> bool:
    """Authorize the implemented coefficient-key family without stage locking.

    Waiting-flock remains an exact frozen fixture and therefore retains its
    full identity/matrix check.  Other stages may reuse the same executable
    source keys and their own source-projected numeric values.  Any additional
    key is left to the engine's fail-closed rejection path.
    """

    present = {
        coefficient.key for enemy in content.enemies for coefficient in enemy.mechanic_coefficients
    }
    if not present or not all(is_runtime_coefficient_key(key) for key in present):
        return False
    handled = False
    if present & WAITING_FLOCK_KEYS:
        if (
            content.bundle_id == WAITING_FLOCK_BUNDLE_ID
            or content.source == WAITING_FLOCK_SOURCE
        ):
            validate_waiting_flock_coefficients(content)
        handled = True
    if present & TIACAUH_BLOCKED_COMBAT_KEYS:
        validate_tiacauh_blocked_combat_coefficients(content)
        handled = True
    if validate_standby_revive_coefficients(content):
        handled = True
    if validate_ghost_hologram_coefficients(content):
        handled = True
    if validate_blade_point_coefficients(content):
        handled = True
    if validate_incoming_damage_scale_coefficients(content):
        handled = True
    if validate_low_hp_attack_coefficients(content):
        handled = True
    if validate_attack_control_coefficients(content):
        handled = True
    if validate_invulnerability_coefficients(content):
        handled = True
    if validate_rear_hit_resistance_coefficients(content):
        handled = True
    if validate_freeze_aura_coefficients(content):
        handled = True
    if validate_rush_acceleration_coefficients(content):
        handled = True
    if validate_defence_aura_coefficients(content):
        handled = True
    if validate_quill_spray_coefficients(content):
        handled = True
    if validate_charge_burst_coefficients(content):
        handled = True
    if validate_attack_speed_aura_coefficients(content):
        handled = True
    if validate_frozen_target_damage_coefficients(content):
        handled = True
    if validate_refracting_coefficients(content):
        handled = True
    if validate_boom_coefficients(content):
        handled = True
    if validate_death_energy_coefficients(content):
        handled = True
    if validate_def_reduce_coefficients(content):
        handled = True
    if validate_periodic_self_damage_coefficients(content):
        handled = True
    if validate_self_decay_ratio_coefficients(content):
        handled = True
    if validate_vampire_coefficients(content):
        handled = True
    if validate_multi_target_coefficients(content):
        handled = True
    if validate_energy_coefficients(content):
        handled = True
    if validate_ranged_mode_coefficients(content):
        handled = True
    if validate_elemental_ratio_coefficients(content):
        handled = True
    if validate_steal_energy_coefficients(content):
        handled = True
    if validate_trait_ability_coefficients(content):
        handled = True
    if validate_taunt_level_coefficients(content):
        handled = True
    if validate_rage_coefficients(content):
        handled = True
    if validate_rush_contact_coefficients(content):
        handled = True
    if validate_crash_contact_coefficients(content):
        handled = True
    if validate_idle_speed_coefficients(content):
        handled = True
    if validate_ember_revive_coefficients(content):
        handled = True
    if validate_blood_debt_coefficients(content):
        handled = True
    if validate_reckoning_coefficients(content):
        handled = True
    if validate_pollution_aura_coefficients(content):
        handled = True
    if validate_attack_stack_coefficients(content):
        handled = True
    if validate_splash_coefficients(content):
        handled = True
    if validate_death_transform(content):
        handled = True
    if validate_mainline12_coefficients(content):
        handled = True
    if validate_return_body_coefficients(content):
        handled = True
    return handled


__all__ = [
    "BLACK_FOG_RANGE_OFFSETS",
    "BLADE_POINT_BUNDLE_ID",
    "BLADE_POINT_COEFFICIENTS",
    "BLADE_POINT_KEYS",
    "BLADE_POINT_SOURCE",
    "GHOST_HOLOGRAM_BUNDLE_ID",
    "GHOST_HOLOGRAM_COEFFICIENTS",
    "GHOST_HOLOGRAM_IDS",
    "GHOST_HOLOGRAM_KEYS",
    "GHOST_HOLOGRAM_SOURCE",
    "AURA_DEFUP_RADIUS_MILLI_TILES",
    "ATTACK_SPEED_AURA_KEYS",
    "ATTACK_SPEED_FRACTION_TO_POINTS",
    "COLD_ATTACK_SPEED_POINTS",
    "FROZEN_MAGIC_RESISTANCE_PENALTY",
    "DEFENCE_AURA_KEYS",
    "FROZEN_TARGET_DAMAGE_KEYS",
    "BOOM_FUSE_TICKS",
    "BOOM_FUSE_TICKS_BY_ENEMY",
    "DEF_REDUCE_KEYS",
    "PERIODIC_SELF_DAMAGE_KEYS",
    "DEATH_TRANSFORM_BLACKBOARD_KEY",
    "RAGE_KEYS",
    "TAUNT_LEVEL_KEYS",
    "death_transform_enemy_id",
    "hologram_enemy_id",
    "TRAIT_ABILITY_BLADE_KEYS",
    "TRAIT_ABILITY_GUARD_KEYS",
    "TRAIT_ABILITY_KEYS",
    "TRAIT_ABILITY_PAIRING_RADIUS_MILLI_TILES",
    "MULTI_TARGET_KEYS",
    "VAMPIRE_KEYS",
    "BOOM_KEYS",
    "BOOM_RADIUS_MILLI_TILES_BY_ENEMY",
    "REFRACTING_KEYS",
    "weakness_protected_side_from_description",
    "CHARGE_BURST_KEYS",
    "QUILL_SPRAY_KEYS",
    "FREEZE_AURA_COEFFICIENTS",
    "FREEZE_AURA_KEYS",
    "GHOST_REDACE_IDS",
    "GHOST_REDACE_TO_HOLOGRAM",
    "MAINLINE12_BUNDLE_ID",
    "MAINLINE12_BRUTE_SPAWN_BINDINGS",
    "MAINLINE12_CIVILIAN_IDS",
    "MAINLINE12_COEFFICIENTS",
    "MAINLINE12_CONTENT_VERSION",
    "MAINLINE12_DETECTION_INTERVAL_TICKS",
    "MAINLINE12_DETECTION_PHASE1_BLOCKED_DELTA",
    "MAINLINE12_DETECTION_PHASE1_THRESHOLD",
    "MAINLINE12_DETECTION_PHASE2_BLOCKED_DELTA",
    "MAINLINE12_DETECTION_PHASE2_THRESHOLD",
    "MAINLINE12_DETECTION_UNBLOCKED_DELTA",
    "MAINLINE12_DISGUISED_IDS",
    "MAINLINE12_ENEMY_DATABASE_SHA256",
    "MAINLINE12_ENHANCED_ATTACK_IDS",
    "MAINLINE12_FIRST_AOE_IDS",
    "MAINLINE12_KEYS",
    "MAINLINE12_IDENTITY_KEYS",
    "MAINLINE12_PACKAGE_HASH",
    "MAINLINE12_REVEALED_INVISIBLE_IDS",
    "MAINLINE12_SOURCE",
    "MAINLINE12_STAGE_ID",
    "MAINLINE12_SWITCH_TRIGGER_TICKS",
    "MAINLINE12_X5_RANGE_OFFSETS",
    "RUNTIME_COEFFICIENT_KEYS",
    "RETURN_BODY_BLEEDING_PULSE_TICKS",
    "RETURN_BODY_BUFF_TEMPLATE_SHA256",
    "RETURN_BODY_BUNDLE_ID",
    "RETURN_BODY_CANNON_BARRAGE_SHOTS",
    "RETURN_BODY_CANNON_IDS",
    "RETURN_BODY_CANNON_RANGE_OFFSETS",
    "RETURN_BODY_CANNON_SHOT_INTERVAL_TICKS",
    "RETURN_BODY_CANNON_WINDUP_TICKS",
    "RETURN_BODY_COEFFICIENTS",
    "RETURN_BODY_CONTENT_VERSION",
    "RETURN_BODY_ENEMY_DATABASE_SHA256",
    "RETURN_BODY_KEYS",
    "RETURN_BODY_PACKAGE_HASH",
    "RETURN_BODY_POLLUTION_PULSE_TICKS",
    "RETURN_BODY_PREFAB_CATALOG_SHA256",
    "RETURN_BODY_SOURCE",
    "RETURN_BODY_STAGE_ID",
    "STANDBY_REVIVE_COEFFICIENTS",
    "STANDBY_REVIVE_ENEMY_IDS",
    "STANDBY_REVIVE_EXCLUSIVE_KEYS",
    "STANDBY_REVIVE_INITIAL_GUARD_IDS",
    "STANDBY_REVIVE_KEYS",
    "STANDBY_REVIVE_MELEE_ARTS_IDS",
    "STANDBY_REVIVE_RANGED_MULTI_TARGET_IDS",
    "TIACAUH_BLOCKED_COMBAT_COEFFICIENTS",
    "TIACAUH_BLOCKED_COMBAT_KEYS",
    "WAITING_FLOCK_KEYS",
    "coefficient_value",
    "validate_runtime_coefficients",
    "validate_blade_point_coefficients",
    "validate_ghost_hologram_coefficients",
    "validate_mainline12_coefficients",
    "validate_return_body_coefficients",
    "validate_standby_revive_coefficients",
    "validate_tiacauh_blocked_combat_coefficients",
    "validate_waiting_flock_coefficients",
]
