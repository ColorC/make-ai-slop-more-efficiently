"""明日方舟战场格子 ↔ 屏幕坐标换算设施。

存在理由（ISR-R18 禁止手算坐标原则）：上层调用方只应表达语义——「点第 3 行第 4 列」、
「开 (4,3) 的技能」——而不应出现任何硬编码像素。所有像素在本模块内部产生。

坐标换算矩阵实现来源：Arknights-Tile-Pos（MIT License, Copyright (c) 2022 yuanyan3060）
    https://github.com/yuanyan3060/Arknights-Tile-Pos
按其 README 要求在此注明出处。本模块为独立实现的 Python 移植，算法与常量取自该仓库
python/main.py，未复制其文件；格子数据取自 MAA 自带的 resource/Arknights-Tile-Pos。

术语：
    tile (col, row)  —— 游戏语义格子坐标，col 为列（x 方向），row 为行（y 方向），均自 0 起。
                        注意 Tile-Pos 原实现的入参顺序是 (tile_x, tile_y) = (col, row)。
    screen (x, y)    —— 屏幕像素，原点左上。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Literal, Sequence

__all__ = [
    "Tile",
    "StageGrid",
    "TileProjector",
    "ProjectedTile",
    "load_stage_grid",
    "CHALLENGE_MARKER",
    "TILE_POS_ATTRIBUTION",
]

TILE_POS_ATTRIBUTION = (
    "tile/screen projection adapted from Arknights-Tile-Pos (MIT), "
    "Copyright (c) 2022 yuanyan3060 — https://github.com/yuanyan3060/Arknights-Tile-Pos"
)

# --- 相机常量，取自 Arknights-Tile-Pos python/main.py ---
_FOV_HALF_DEG = 20.0
_ROT_X_DEG = 30.0
_ROT_Y_DEG = 10.0
_NEAR = 0.3
_FAR = 1000.0
# 撤退/技能按钮相对干员世界坐标的固定偏移
_BUTTON_DX = 1.3143386840820312
_BUTTON_DY = 1.314337134361267
_BUTTON_Z = -0.3967874050140381
# 宽高比补偿区间
_RATIO_FROM = 9 / 16
_RATIO_TO = 3 / 4
_ADAPT_Y = -1.4
_ADAPT_Z = -2.8

HeightType = Literal[0, 1]
"""0 = 地面（近战位高度），1 = 高台。"""

BuildableType = Literal[0, 1, 2]
"""0 = 不可部署，1 = 仅近战（地面），2 = 仅远程（高台）。"""

CHALLENGE_MARKER = "#f#"
"""stageId 中的突袭（challenge / raid）变体标记。

同一个 ``code``（如 ``1-7``）在 MAA 的 Arknights-Tile-Pos 里可能对应两个文件：
``main_01-07``（普通）与 ``main_01-07#f#``（突袭）。二者 ``levelId`` 相同、地图尺寸相同，
但相机 ``view`` 与地形可能有差异，因此必须显式区分，不能任取其一。
"""

Variant = Literal["normal", "challenge"]


@dataclass(frozen=True, slots=True)
class Tile:
    """一个格子的静态属性。"""

    height_type: int
    buildable_type: int
    tile_key: str = ""
    is_start: bool = False
    is_end: bool = False

    @property
    def is_ground(self) -> bool:
        return self.height_type == 0

    @property
    def is_highland(self) -> bool:
        return self.height_type == 1

    @property
    def deployable_melee(self) -> bool:
        return self.buildable_type == 1

    @property
    def deployable_ranged(self) -> bool:
        return self.buildable_type == 2

    @property
    def deployable(self) -> bool:
        return self.buildable_type in (1, 2)


@dataclass(frozen=True, slots=True)
class ProjectedTile:
    """一次投影结果。"""

    col: int
    row: int
    x: float
    y: float

    @property
    def tile(self) -> tuple[int, int]:
        return (self.col, self.row)


@dataclass(frozen=True, slots=True)
class StageGrid:
    """一张关卡地图的格子矩阵与相机参数。

    ``view`` 为两组三元组：``view[0]`` 常规视角，``view[1]`` 侧视（拖拽干员时相机倾斜）。
    """

    stage_id: str
    code: str
    name: str
    level_id: str
    width: int
    height: int
    tiles: tuple[tuple[Tile, ...], ...]
    view: tuple[tuple[float, float, float], tuple[float, float, float]]

    @property
    def variant(self) -> str:
        """``"challenge"`` 表示突袭变体，否则 ``"normal"``。"""
        return "challenge" if CHALLENGE_MARKER in self.stage_id else "normal"

    def tile_at(self, col: int, row: int) -> Tile:
        if not (0 <= col < self.width and 0 <= row < self.height):
            raise IndexError(
                f"tile ({col},{row}) out of range for {self.code} "
                f"({self.width}x{self.height})"
            )
        return self.tiles[row][col]

    def iter_tiles(self) -> Iterator[tuple[int, int, Tile]]:
        for row in range(self.height):
            for col in range(self.width):
                yield col, row, self.tiles[row][col]

    def deployable_tiles(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, t in self.iter_tiles() if t.deployable]


def _mat_mul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    n, m, p = len(a), len(b), len(b[0])
    return [
        [sum(a[i][k] * b[k][j] for k in range(m)) for j in range(p)]
        for i in range(n)
    ]


def _mat_vec(a: Sequence[Sequence[float]], v: Sequence[float]) -> list[float]:
    return [sum(row[i] * v[i] for i in range(len(v))) for row in a]


class TileProjector:
    """把语义格子坐标换算成屏幕像素，以及反向归属。

    纯 stdlib 实现（不依赖 numpy），便于在任何环境内使用。
    """

    def __init__(self, screen_width: int, screen_height: int, grid: StageGrid) -> None:
        if screen_width <= 0 or screen_height <= 0:
            raise ValueError("screen size must be positive")
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.grid = grid
        self._ratio = screen_height / screen_width

        t = math.tan(math.pi * _FOV_HALF_DEG / 180)
        self._matrix_p = [
            [self._ratio / t, 0.0, 0.0, 0.0],
            [0.0, 1 / t, 0.0, 0.0],
            [
                0.0,
                0.0,
                -(_FAR + _NEAR) / (_FAR - _NEAR),
                -(_FAR * _NEAR * 2) / (_FAR - _NEAR),
            ],
            [0.0, 0.0, -1.0, 0.0],
        ]
        cx, sx = (
            math.cos(math.pi * _ROT_X_DEG / 180),
            math.sin(math.pi * _ROT_X_DEG / 180),
        )
        self._matrix_x = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cx, -sx, 0.0],
            [0.0, -sx, -cx, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        cy, sy = (
            math.cos(math.pi * _ROT_Y_DEG / 180),
            math.sin(math.pi * _ROT_Y_DEG / 180),
        )
        self._matrix_y = [
            [cy, 0.0, sy, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [-sy, 0.0, cy, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        self._cache: dict[tuple[int, int, bool, bool], tuple[float, float]] = {}

    # ---- 内部几何 ----

    def _adapter(self) -> tuple[float, float]:
        if self._ratio < _RATIO_FROM - 1e-5:
            return 0.0, 0.0
        t = (self._ratio - _RATIO_FROM) / (_RATIO_TO - _RATIO_FROM)
        return _ADAPT_Y * t, _ADAPT_Z * t

    def _focus_offset(self, col: int, row: int) -> tuple[float, float, float]:
        return (
            col - (self.grid.width - 1) / 2,
            (self.grid.height - 1) / 2 - row,
            0.0,
        )

    def _character_world(self, col: int, row: int) -> tuple[float, float, float]:
        tile = self.grid.tile_at(col, row)
        return (
            col - (self.grid.width - 1) / 2,
            (self.grid.height - 1) / 2 - row,
            tile.height_type * -0.4,
        )

    def _world_to_screen(
        self,
        pos: tuple[float, float, float],
        side: bool,
        offset: tuple[float, float, float],
    ) -> tuple[float, float]:
        ay, az = self._adapter()
        vx, vy, vz = self.grid.view[1] if side else self.grid.view[0]
        vx += offset[0]
        vy += offset[1] + ay
        vz += offset[2] + az
        raw = [
            [1.0, 0.0, 0.0, -vx],
            [0.0, 1.0, 0.0, -vy],
            [0.0, 0.0, 1.0, -vz],
            [0.0, 0.0, 0.0, 1.0],
        ]
        if side:
            m = _mat_mul(_mat_mul(self._matrix_x, self._matrix_y), raw)
        else:
            m = _mat_mul(self._matrix_x, raw)
        m = _mat_mul(self._matrix_p, m)
        x, y, _z, w = _mat_vec(m, [pos[0], pos[1], pos[2], 1.0])
        nx = (1 + x / w) / 2
        ny = (1 + y / w) / 2
        return nx * self.screen_width, (1 - ny) * self.screen_height

    # ---- 公开接口：语义进，像素出 ----

    def tile_center(
        self, col: int, row: int, *, side: bool = False, focus: bool = False
    ) -> tuple[float, float]:
        """格心屏幕坐标。``focus=True`` 表示该格已被选中（相机聚焦并侧倾）。"""
        if focus:
            side = True
        key = (col, row, side, focus)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        world = self._character_world(col, row)
        offset = self._focus_offset(col, row) if focus else (0.0, 0.0, 0.0)
        out = self._world_to_screen(world, side, offset)
        self._cache[key] = out
        return out

    def retreat_button(self, col: int, row: int) -> tuple[float, float]:
        """已部署干员的「撤退」按钮屏幕坐标（该干员被选中时）。"""
        wx, wy, _ = self._character_world(col, row)
        world = (wx - _BUTTON_DX, wy + _BUTTON_DY, _BUTTON_Z)
        return self._world_to_screen(world, True, self._focus_offset(col, row))

    def skill_button(self, col: int, row: int) -> tuple[float, float]:
        """已部署干员的「技能」按钮屏幕坐标（该干员被选中时）。"""
        wx, wy, _ = self._character_world(col, row)
        world = (wx + _BUTTON_DX, wy - _BUTTON_DY, _BUTTON_Z)
        return self._world_to_screen(world, True, self._focus_offset(col, row))

    def project_all(
        self, *, side: bool = False, focus: bool = False
    ) -> list[ProjectedTile]:
        out = []
        for row in range(self.grid.height):
            for col in range(self.grid.width):
                x, y = self.tile_center(col, row, side=side, focus=focus)
                out.append(ProjectedTile(col, row, x, y))
        return out

    def on_screen(self, col: int, row: int, *, side: bool = False) -> bool:
        x, y = self.tile_center(col, row, side=side)
        return 0 <= x <= self.screen_width and 0 <= y <= self.screen_height

    def offscreen_tiles(self, *, side: bool = False) -> list[tuple[int, int]]:
        return [
            (c, r)
            for c, r, _ in self.grid.iter_tiles()
            if not self.on_screen(c, r, side=side)
        ]

    # ---- 反向归属：像素 → 格子 ----

    def tile_from_screen(
        self, x: float, y: float, *, side: bool = False, max_distance: float | None = None
    ) -> tuple[int, int] | None:
        """把屏幕像素归属到最近的格子。

        ``max_distance`` 为像素距离上限；超出则返回 ``None``（宁可不判也不误判）。
        """
        best: tuple[int, int] | None = None
        best_d2 = float("inf")
        for pt in self.project_all(side=side):
            d2 = (pt.x - x) ** 2 + (pt.y - y) ** 2
            if d2 < best_d2:
                best_d2, best = d2, (pt.col, pt.row)
        if best is None:
            return None
        if max_distance is not None and math.sqrt(best_d2) > max_distance:
            return None
        return best

    def min_center_spacing(self, *, side: bool = False) -> float:
        """相邻格心的最小屏幕间距。用于判断反向归属的容差上界。"""
        pts = {(p.col, p.row): (p.x, p.y) for p in self.project_all(side=side)}
        best = float("inf")
        for (c, r), (x, y) in pts.items():
            for dc, dr in ((1, 0), (0, 1)):
                nb = pts.get((c + dc, r + dr))
                if nb is None:
                    continue
                best = min(best, math.dist((x, y), nb))
        return best


# --- 关卡格子数据加载 ---


def _tiles_from_rows(rows: Iterable[Iterable[dict]]) -> tuple[tuple[Tile, ...], ...]:
    return tuple(
        tuple(
            Tile(
                height_type=int(t["heightType"]),
                buildable_type=int(t["buildableType"]),
                tile_key=str(t.get("tileKey", "")),
                is_start=bool(t.get("isStart", False)),
                is_end=bool(t.get("isEnd", False)),
            )
            for t in row
        )
        for row in rows
    )


def _grid_from_json(d: dict) -> StageGrid:
    view = d["view"]
    return StageGrid(
        stage_id=str(d.get("stageId", "")),
        code=str(d.get("code", "")),
        name=str(d.get("name", "")),
        level_id=str(d.get("levelId", "")),
        width=int(d["width"]),
        height=int(d["height"]),
        tiles=_tiles_from_rows(d["tiles"]),
        view=(
            (float(view[0][0]), float(view[0][1]), float(view[0][2])),
            (float(view[1][0]), float(view[1][1]), float(view[1][2])),
        ),
    )


def load_stage_grid(
    tile_pos_dir: Path,
    *,
    code: str | None = None,
    stage_id: str | None = None,
    variant: Variant = "normal",
) -> StageGrid:
    """从 MAA 的 ``resource/Arknights-Tile-Pos`` 目录按 code 或 stageId 载入一张关卡。

    该目录为每关一个 JSON，文件名形如 ``{stageId}-{levelId 的 / 换成 -}.json``。

    :param code: 关卡代号，如 ``"1-7"``。
    :param stage_id: 精确 stageId，如 ``"main_01-07"``；给定时 ``variant`` 被忽略。
    :param variant: 按 ``code`` 查询时选普通还是突袭变体（见 :data:`CHALLENGE_MARKER`）。
    :raises LookupError: 找不到，或同一 code+variant 仍对应多个 stageId（如集成战略的
        ``ISW-NO`` 有数百个同 code 关卡），此时必须改用 ``stage_id`` 精确指定。
    """
    if not (code or stage_id):
        raise ValueError("need code or stage_id")
    if variant not in ("normal", "challenge"):
        raise ValueError(f"unknown variant: {variant!r}")
    tile_pos_dir = Path(tile_pos_dir)
    if not tile_pos_dir.is_dir():
        raise FileNotFoundError(f"tile-pos dir not found: {tile_pos_dir}")

    candidates: Iterable[Path]
    if stage_id:
        candidates = sorted(tile_pos_dir.glob(f"{stage_id}-*.json"))
        if not candidates:
            candidates = sorted(tile_pos_dir.glob("*.json"))
    else:
        candidates = sorted(tile_pos_dir.glob("*.json"))

    matches: list[StageGrid] = []
    for path in candidates:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if stage_id:
            if str(d.get("stageId", "")) != stage_id:
                continue
        elif code and str(d.get("code", "")) != code:
            continue
        matches.append(_grid_from_json(d))

    if not matches:
        raise LookupError(f"no stage grid for code={code!r} stage_id={stage_id!r}")

    if stage_id:
        return matches[0]

    # 按 code 查询：先用 variant 收窄普通/突袭
    wanted = [m for m in matches if m.variant == variant]
    if not wanted:
        available = sorted({m.variant for m in matches})
        raise LookupError(
            f"code={code!r} has no {variant!r} variant; available: {available}"
        )
    unique_ids = sorted({m.stage_id for m in wanted})
    if len(unique_ids) > 1:
        preview = unique_ids[:6]
        more = "" if len(unique_ids) <= 6 else f" (+{len(unique_ids) - 6} more)"
        raise LookupError(
            f"code={code!r} variant={variant!r} matches {len(unique_ids)} stageIds "
            f"{preview}{more}; pass stage_id to disambiguate"
        )
    return wanted[0]
