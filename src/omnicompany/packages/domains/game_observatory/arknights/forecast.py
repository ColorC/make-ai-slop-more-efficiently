"""战场预报设施：开打前从关卡数据推出可核对的战场事实。

**范围界定（ANS-R10）**：只做**数据驱动预报**，不做伤害演算。生态内不存在可用的战斗逻辑
重实现（见调研 03），自造模拟器是另一个量级的工程，也不是本阶段目标。因此本模块只回答
「这关会来多少敌人、什么类型、从哪出、什么时候出、走哪条路、我最多能漏几个」——
全部是关卡文件里客观存在的事实，因此既简单又可证伪。

数据来源
--------
* 关卡定义：``ArknightsGameResource/gamedata/levels/**/level_*.json``
* 敌人属性：``gamedata/levels/enemydata/enemy_database.json``

已被实测反证／证实的规则
------------------------
* ``actions[].actionType`` 中**只有 0 是出怪**。1-7 若把 ``actionType==1`` 也计入会得 43，
  而实战击杀分母为 **41**，与只计 0 的求和一致 —— 这条规则是被实测反证出来的，不是猜的。
* ``options.maxLifePoint`` 与实战 UI 的生命值一致（1-7 均为 10）。
* ``mapData.map`` 的行列数与格子设施读到的 ``11x7`` 一致。

时序模型（**部分待核对**）
--------------------------
波次顺序执行；波内 fragment 顺序执行，``fragment.preDelay`` 相对上一 fragment 结束计时；
``action.preDelay`` 相对所属 fragment 起点计时；``count>1`` 时按 ``interval`` 秒依次出怪。
其中「fragment 之间如何串接」尚未用帧时间戳核对，故 :attr:`SpawnEvent.time` 标注为
``timing_verified=False``，并在可核对项清单里单列。不把未核对的东西说成已核对。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

__all__ = [
    "ForecastError",
    "Position",
    "RouteInfo",
    "SpawnEvent",
    "StageForecast",
    "StageOptions",
    "EnemyStats",
    "load_forecast",
    "CHECKABLE_ITEMS",
]


class ForecastError(RuntimeError):
    """关卡数据缺失或结构不符预期。"""


#: 出怪的 actionType。**由实测反证确定**：1-7 只计 0 得 41，与击杀分母吻合；
#: 若并入 1 则得 43，与实测矛盾。
SPAWN_ACTION_TYPE = 0


@dataclass(frozen=True, slots=True)
class Position:
    row: int
    col: int

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> "Position | None":
        if not d:
            return None
        return cls(row=int(d.get("row", 0)), col=int(d.get("col", 0)))

    def as_tile(self) -> tuple[int, int]:
        """转成格子设施的 ``(col, row)`` 约定。"""
        return self.col, self.row


@dataclass(frozen=True, slots=True)
class RouteInfo:
    index: int
    start: Position | None
    end: Position | None
    motion_mode: int
    checkpoints: tuple[Position, ...]

    @property
    def is_flying(self) -> bool:
        """``motionMode`` 1 为飞行。0 为地面，2 见于非出怪路线。"""
        return self.motion_mode == 1


@dataclass(frozen=True, slots=True)
class SpawnEvent:
    """一个敌人的出场预报。"""

    enemy_id: str
    time: float
    route_index: int
    wave_index: int
    fragment_index: int
    action_index: int
    ordinal_in_action: int
    timing_verified: bool = False


@dataclass(frozen=True, slots=True)
class StageOptions:
    character_limit: int
    max_life_point: int
    initial_cost: int
    max_cost: int
    cost_increase_time: float
    move_multiplier: float

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "StageOptions":
        return cls(
            character_limit=int(d.get("characterLimit", 0)),
            max_life_point=int(d.get("maxLifePoint", 0)),
            initial_cost=int(d.get("initialCost", 0)),
            max_cost=int(d.get("maxCost", 0)),
            cost_increase_time=float(d.get("costIncreaseTime", 1.0)),
            move_multiplier=float(d.get("moveMultiplier", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class EnemyStats:
    """敌人基础属性。``overwritten`` 为关卡级覆盖后的最终值。"""

    enemy_id: str
    name: str
    hp: int | None
    atk: int | None
    defence: int | None
    res: float | None
    is_flying: bool
    immunities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageForecast:
    """一关的完整预报。每个字段都对应一个可核对项。"""

    stage_file: str
    map_width: int
    map_height: int
    options: StageOptions
    enemy_refs: tuple[str, ...]
    spawns: tuple[SpawnEvent, ...]
    routes: tuple[RouteInfo, ...]
    deployable_melee: tuple[tuple[int, int], ...]
    deployable_ranged: tuple[tuple[int, int], ...]
    enemy_stats: dict[str, EnemyStats] = field(default_factory=dict)

    # ---- 可核对量 ----

    @property
    def total_enemies(self) -> int:
        """敌人总数。对应实战 UI 的击杀分母。"""
        return len(self.spawns)

    @property
    def enemy_types(self) -> tuple[str, ...]:
        """实际出场的敌人类型集合（按出场顺序去重）。

        注意与 :attr:`enemy_refs` 区分：后者是关卡声明的引用表，可能包含未实际出场的条目。
        """
        seen: dict[str, None] = {}
        for s in self.spawns:
            seen.setdefault(s.enemy_id, None)

        return tuple(seen)

    @property
    def spawn_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.spawns:
            out[s.enemy_id] = out.get(s.enemy_id, 0) + 1
        return out

    @property
    def spawn_positions(self) -> tuple[tuple[int, int], ...]:
        """去重后的出生格（``(col, row)``）。"""
        seen: dict[tuple[int, int], None] = {}
        for s in self.spawns:
            r = self.route(s.route_index)
            if r and r.start:
                seen.setdefault(r.start.as_tile(), None)
        return tuple(seen)

    @property
    def allowed_leaks(self) -> int:
        """允许漏怪数 ＝ 最大生命值。漏满即失败。"""
        return self.options.max_life_point

    @property
    def three_star_requires_no_leak(self) -> bool:
        """三星是否要求零漏怪。

        主题关卡的三星条件是**生命值不减**，故等价于零漏怪。此判定用于把
        「三星」当作稳定性判据（ANS-R03）时的口径说明。
        """
        return True

    def route(self, index: int) -> RouteInfo | None:
        for r in self.routes:
            if r.index == index:
                return r
        return None

    def timeline(self) -> Iterator[SpawnEvent]:
        """按预报时刻排序的出怪序列。"""
        return iter(sorted(self.spawns, key=lambda s: (s.time, s.route_index)))

    def summary(self) -> dict[str, Any]:
        """机读预报摘要，用于写盘与对账。"""
        return {
            "stage_file": self.stage_file,
            "map": {"width": self.map_width, "height": self.map_height},
            "options": {
                "character_limit": self.options.character_limit,
                "max_life_point": self.options.max_life_point,
                "initial_cost": self.options.initial_cost,
                "max_cost": self.options.max_cost,
                "cost_increase_time": self.options.cost_increase_time,
                "move_multiplier": self.options.move_multiplier,
            },
            "total_enemies": self.total_enemies,
            "enemy_types": list(self.enemy_types),
            "spawn_counts": self.spawn_counts,
            "spawn_positions": [list(p) for p in self.spawn_positions],
            "allowed_leaks": self.allowed_leaks,
            "route_count": len(self.routes),
            "deployable_melee": len(self.deployable_melee),
            "deployable_ranged": len(self.deployable_ranged),
            "timing_verified": all(s.timing_verified for s in self.spawns),
        }


#: 冻结的可核对项清单（ANS-R11）。``verifiable`` 为 False 的项必须显式列出理由，
#: 不允许悄悄从对账里省掉 —— 那会把 100% 做成空话。
CHECKABLE_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "key": "total_enemies",
        "how": "战斗 UI 击杀计数的分母（OCR）",
        "verifiable": True,
        "note": "1-7 已实测吻合：预报 41，实测 32/41",
    },
    {
        "key": "max_life_point",
        "how": "战斗 UI 生命值初值（OCR）",
        "verifiable": True,
        "note": "1-7 已实测吻合：预报 10，实测 10",
    },
    {
        "key": "initial_cost",
        "how": "战斗开始瞬间的费用读数（OCR）",
        "verifiable": True,
        "note": "需要战斗最初若干帧；本轮采帧未覆盖开局，待补",
    },
    {
        "key": "cost_increase_time",
        "how": "连续两帧费用增量与时间差之比",
        "verifiable": True,
        "note": "需要稳定帧率下的连续帧",
    },
    {
        "key": "enemy_types",
        "how": "自建敌人感知输出的类型集合",
        "verifiable": True,
        "note": "受 ANS-R06 感知门限约束；门限未达标前不计入达成判定",
    },
    {
        "key": "spawn_counts",
        "how": "自建敌人感知的按类型计数",
        "verifiable": True,
        "note": "同上，依赖感知准确率",
    },
    {
        "key": "spawn_positions",
        "how": "敌人首次出现的格子（感知 ＋ 格子反查）",
        "verifiable": True,
        "note": "依赖投影对齐（ANS-R02）先达标",
    },
    {
        "key": "spawn_times",
        "how": "帧时间戳与出怪时刻对齐",
        "verifiable": False,
        "reason": "fragment 之间的串接语义尚未核对，且当前采帧间隔约 1.2s、"
                  "战斗有 2 倍速，时间分辨率不足以逐个出怪对齐。需先做定速采帧。",
    },
    {
        "key": "route_checkpoints",
        "how": "敌人逐帧位置序列与路径关键点比对",
        "verifiable": False,
        "reason": "需要敌人跟踪（跨帧关联同一实体），尚未实现。",
    },
    {
        "key": "deployable_tiles",
        "how": "格子设施的 buildableType 与实际可部署位比对",
        "verifiable": True,
        "note": "可用 Tile-Pos 数据交叉验证，无需感知",
    },
)


# ---------- 加载 ----------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ForecastError(f"关卡数据不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ForecastError(f"关卡数据不是合法 JSON: {path}: {exc}") from exc


@lru_cache(maxsize=4)
def _enemy_database(path_str: str) -> dict[str, dict[str, Any]]:
    """把 ``enemy_database.json`` 索引成 ``enemy_id -> 条目``。

    注意该文件使用**小写** ``key``／``value``，与 FBS schema 里的 ``Key``／``Value``
    不同 —— 这点此前已踩过。
    """
    data = _read_json(Path(path_str))
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("enemies", []) or []:
        eid = item.get("Key") or item.get("key")
        if eid:
            out[eid] = item
    return out


def _pick(d: dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in d:
            return d[n]
    return None


def _enemy_stats(eid: str, db: dict[str, dict[str, Any]]) -> EnemyStats | None:
    entry = db.get(eid)
    if not entry:
        return None
    values = _pick(entry, "Value", "value") or []
    if not values:
        return None
    # 取 level 0 的定义
    first = values[0]
    ed = _pick(first, "enemyData", "EnemyData") or {}

    def num(field_name: str) -> Any:
        blk = ed.get("attributes", {}).get(field_name) if ed.get("attributes") else None
        if isinstance(blk, dict):
            return blk.get("m_value")
        return None

    name_blk = ed.get("name") or {}
    name = name_blk.get("m_value") if isinstance(name_blk, dict) else None
    mt = ed.get("motionType") or {}
    motion = mt.get("m_value") if isinstance(mt, dict) else None
    imm: list[str] = []
    attrs = ed.get("attributes") or {}
    for k, v in attrs.items():
        if k.startswith("immune") and isinstance(v, dict) and v.get("m_value"):
            imm.append(k)
    return EnemyStats(
        enemy_id=eid,
        name=name or eid,
        hp=num("maxHp"),
        atk=num("atk"),
        defence=num("def"),
        res=num("magicResistance"),
        is_flying=(motion == "FLY"),
        immunities=tuple(sorted(imm)),
    )


def _iter_spawns(waves: Sequence[dict[str, Any]]) -> Iterator[SpawnEvent]:
    """按时序模型展开出怪。

    时刻计算：``wave.preDelay`` ＋ 各 fragment 累积 ＋ ``action.preDelay``
    ＋ ``ordinal * interval``。fragment 串接语义未经帧时间戳核对，故
    ``timing_verified=False``。
    """
    wave_clock = 0.0
    for wi, wave in enumerate(waves or []):
        wave_clock += float(wave.get("preDelay", 0.0) or 0.0)
        frag_clock = wave_clock
        for fi, frag in enumerate(wave.get("fragments", []) or []):
            frag_clock += float(frag.get("preDelay", 0.0) or 0.0)
            frag_end = frag_clock
            for ai, act in enumerate(frag.get("actions", []) or []):
                if int(act.get("actionType", -1)) != SPAWN_ACTION_TYPE:
                    continue
                key = act.get("key")
                if not key:
                    continue
                count = int(act.get("count", 0) or 0)
                interval = float(act.get("interval", 0.0) or 0.0)
                pre = float(act.get("preDelay", 0.0) or 0.0)
                for n in range(count):
                    t = frag_clock + pre + n * interval
                    frag_end = max(frag_end, t)
                    yield SpawnEvent(
                        enemy_id=str(key),
                        time=round(t, 3),
                        route_index=int(act.get("routeIndex", -1)),
                        wave_index=wi,
                        fragment_index=fi,
                        action_index=ai,
                        ordinal_in_action=n,
                        timing_verified=False,
                    )
            frag_clock = frag_end
        wave_clock = frag_clock + float(wave.get("postDelay", 0.0) or 0.0)


def _routes(raw: Iterable[dict[str, Any] | None]) -> tuple[RouteInfo, ...]:
    out: list[RouteInfo] = []
    for i, r in enumerate(raw or []):
        if not r:
            continue
        cps = tuple(
            p
            for p in (
                Position.from_json(c.get("position"))
                for c in (r.get("checkpoints") or [])
                if isinstance(c, dict)
            )
            if p is not None
        )
        out.append(
            RouteInfo(
                index=i,
                start=Position.from_json(r.get("startPosition")),
                end=Position.from_json(r.get("endPosition")),
                motion_mode=int(r.get("motionMode", 0) or 0),
                checkpoints=cps,
            )
        )
    return tuple(out)


def _deployable(map_data: dict[str, Any]) -> tuple[
    tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]
]:
    """从 ``mapData.tiles`` 取可部署格。

    ``buildableType``：0 不可部署、1 仅近战、2 仅远程。与格子设施的约定一致。
    """
    grid = map_data.get("map") or []
    tiles = map_data.get("tiles") or []
    melee: list[tuple[int, int]] = []
    ranged: list[tuple[int, int]] = []
    for row_idx, row in enumerate(grid):
        for col_idx, tile_idx in enumerate(row):
            if not isinstance(tile_idx, int) or tile_idx < 0 or tile_idx >= len(tiles):
                continue
            bt = tiles[tile_idx].get("buildableType")
            if bt == 1:
                melee.append((col_idx, row_idx))
            elif bt == 2:
                ranged.append((col_idx, row_idx))
    return tuple(melee), tuple(ranged)


def load_forecast(
    level_path: Path | str, *, enemy_db: Path | str | None = None
) -> StageForecast:
    """从关卡 JSON 生成预报。

    :param level_path: ``level_*.json`` 路径。
    :param enemy_db: ``enemy_database.json`` 路径。省略则不填充敌人属性。
    """
    path = Path(level_path)
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ForecastError(f"关卡数据顶层不是对象: {path}")

    map_data = data.get("mapData") or {}
    grid = map_data.get("map") or []
    height = len(grid)
    width = len(grid[0]) if height else 0
    if not width or not height:
        raise ForecastError(f"关卡地图为空: {path}")

    spawns = tuple(_iter_spawns(data.get("waves") or []))
    refs = tuple(
        str(r.get("id"))
        for r in (data.get("enemyDbRefs") or [])
        if isinstance(r, dict) and r.get("id")
    )
    melee, ranged = _deployable(map_data)

    stats: dict[str, EnemyStats] = {}
    if enemy_db is not None:
        db = _enemy_database(str(Path(enemy_db)))
        for eid in {s.enemy_id for s in spawns} | set(refs):
            st = _enemy_stats(eid, db)
            if st:
                stats[eid] = st

    return StageForecast(
        stage_file=path.name,
        map_width=width,
        map_height=height,
        options=StageOptions.from_json(data.get("options") or {}),
        enemy_refs=refs,
        spawns=spawns,
        routes=_routes(data.get("routes") or []),
        deployable_melee=melee,
        deployable_ranged=ranged,
        enemy_stats=stats,
    )
