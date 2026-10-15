"""从单张战斗帧生成带来源通道的结构化状态。

本模块先建立 ANS-R04 的稳定契约，再逐项接入感知器。未知字段必须显式保留为
``known=false``，不能用关卡预报、MAA 任务回调或默认值冒充画面观测。

当前已接通战斗 HUD、部署卡片结构／费用／部分头像身份，以及带关卡网格约束的场上己方
位置、当前冻结样本覆盖的朝向／技能状态和敌方可见交战压力。场上姓名、未覆盖的分类情形
和敌人个体类型仍在相应模型通过更完整人工标注门限前保持未知。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from omnicompany.core.pycompat import StrEnum
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SourcePixelRect
from ..recognition_service import RecognitionService
from .tile_grid import StageGrid, TileProjector

__all__ = [
    "BattleStateV1",
    "BattleStateParser",
    "BattlefieldOperatorReadV1",
    "BattlefieldOperatorsReadV1",
    "BattlefieldOperatorsReader",
    "COST_REGION",
    "COST_PROGRESS_REGION",
    "DeploymentCardReadV1",
    "DeploymentCardReader",
    "DeploymentCardsReadV1",
    "DeploymentCardsReader",
    "DeployableOperatorV1",
    "EnemyBattleStateV1",
    "EnemyPressureReadV1",
    "EnemyPressureReader",
    "EnemyPressureV1",
    "EnemyUnitV1",
    "FieldEvidenceV1",
    "FriendlyBattleStateV1",
    "FriendlyOperatorV1",
    "GlobalBattleStateV1",
    "KILLS_REGION",
    "LIFE_REGION",
    "ObservedV1",
    "OCRRead",
    "OCRToken",
    "PAUSE_REGION",
    "PixelRegionV1",
    "RapidOCRBattleReader",
    "RedHealthBarEnemyPressureReader",
    "ROADBLOCK_NAME",
    "MaaBattlefieldOperatorReader",
    "RegionOCR",
    "SourceChannel",
    "battle_state_schema",
    "parse_battle_frame",
]

CANONICAL_WIDTH = 1280
CANONICAL_HEIGHT = 720

# 识别器内部的规范空间 ROI。调用方只提供帧，不接触像素坐标。
KILLS_REGION = SourcePixelRect(x=500, y=0, width=170, height=65)
LIFE_REGION = SourcePixelRect(x=675, y=0, width=160, height=70)
COST_REGION = SourcePixelRect(x=1140, y=475, width=140, height=80)
COST_PROGRESS_REGION = SourcePixelRect(x=1159, y=540, width=121, height=5)
PAUSE_REGION = SourcePixelRect(x=450, y=245, width=380, height=175)
ROADBLOCK_NAME = "障碍物"

_KILLS = re.compile(r"^(\d{1,3})\s*/\s*(\d{1,3})$")
_INTEGER = re.compile(r"^\d{1,3}$")


def _cv_imread(path: Path) -> Any:
    """Decode a path without relying on OpenCV's Windows Unicode handling."""
    import cv2  # type: ignore[import-not-found]
    import numpy as np

    payload = np.fromfile(str(path), dtype=np.uint8)
    if not payload.size:
        return None
    return cv2.imdecode(payload, cv2.IMREAD_COLOR)


class SourceChannel(StrEnum):
    """计划允许的感知来源通道。"""

    MAA_MODEL = "maa_model"
    MAA_OCR = "maa_ocr"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class PixelRegionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class FieldEvidenceV1(BaseModel):
    """一个字段的感知真源，而不是推导来源说明。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: SourceChannel
    backend: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    region: PixelRegionV1 | None = None
    raw_text: tuple[str, ...] = ()


T = TypeVar("T")


class ObservedV1(BaseModel, Generic[T]):
    """显式区分已知空集合、已知假值与未知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    known: bool
    value: T | None = None
    evidence: FieldEvidenceV1
    reason: str | None = None

    @model_validator(mode="after")
    def known_value_contract(self) -> "ObservedV1[T]":
        if self.known and self.value is None:
            raise ValueError("known observation must contain a value")
        if not self.known and self.value is not None:
            raise ValueError("unknown observation cannot contain a value")
        if not self.known and not self.reason:
            raise ValueError("unknown observation must explain why it is unknown")
        return self


class TileCoordinateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    col: int = Field(ge=0)
    row: int = Field(ge=0)


class ScreenPointV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0)
    y: float = Field(ge=0)


class FriendlyOperatorV1(BaseModel):
    """己方对象；各属性可来自不同模型，故逐字段携带来源。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ObservedV1[str]
    tile: ObservedV1[TileCoordinateV1]
    direction: ObservedV1[str]
    skill_ready: ObservedV1[bool]


class DeployableOperatorV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: ObservedV1[int]
    card_region: ObservedV1[PixelRegionV1]
    name: ObservedV1[str]
    cost: ObservedV1[int]
    available: ObservedV1[bool]
    cooling: ObservedV1[bool]


class EnemyUnitV1(BaseModel):
    """敌方自建增量的目标契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: ObservedV1[ScreenPointV1]
    enemy_type: ObservedV1[str]
    health_ratio: ObservedV1[float]
    abnormal_statuses: ObservedV1[tuple[str, ...]]


class EnemyPressureV1(BaseModel):
    """只陈述画面中可验证的交战压力，不把未显血条的敌人伪造成不存在。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible_health_bars: int = Field(ge=0)
    top_lane: int = Field(ge=0)
    middle_lane: int = Field(ge=0)
    bottom_lane: int = Field(ge=0)
    closest_screen_x: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "EnemyPressureV1":
        if self.top_lane + self.middle_lane + self.bottom_lane != self.visible_health_bars:
            raise ValueError("enemy pressure lane counts must equal visible_health_bars")
        if (self.visible_health_bars == 0) != (self.closest_screen_x is None):
            raise ValueError("closest_screen_x must be present exactly when pressure is visible")
        return self


class FriendlyBattleStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deployed: ObservedV1[tuple[FriendlyOperatorV1, ...]]
    deployable: ObservedV1[tuple[DeployableOperatorV1, ...]]


class EnemyBattleStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    units: ObservedV1[tuple[EnemyUnitV1, ...]]
    visible_pressure: ObservedV1[EnemyPressureV1] = Field(
        default_factory=lambda: ObservedV1(
            known=False,
            evidence=FieldEvidenceV1(
                channel=SourceChannel.UNKNOWN,
                backend="not-observed",
            ),
            reason="enemy pressure reader was not supplied",
        )
    )


class GlobalBattleStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_cost: ObservedV1[int]
    cost_progress: ObservedV1[float] = Field(
        default_factory=lambda: ObservedV1(
            known=False,
            evidence=FieldEvidenceV1(
                channel=SourceChannel.UNKNOWN,
                backend="not-observed",
            ),
            reason="cost progress reader was not supplied",
        )
    )
    kills: ObservedV1[int]
    total_enemies: ObservedV1[int]
    life_points: ObservedV1[int]
    paused: ObservedV1[bool]


class FrameReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class BattleStateV1(BaseModel):
    """ANS-R04 单帧战场状态契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: str = Field(default="omnicompany.arknights.battle-state.v1", alias="schema")
    frame: FrameReferenceV1
    friendly: FriendlyBattleStateV1
    enemies: EnemyBattleStateV1
    global_state: GlobalBattleStateV1 = Field(alias="global")


class OCRToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    confidence: float = Field(ge=0, le=1)


class OCRRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1)
    tokens: tuple[OCRToken, ...] = ()


class RegionOCR(Protocol):
    def __call__(self, image_path: Path, region: SourcePixelRect) -> OCRRead: ...


class DeploymentCardReadV1(BaseModel):
    """底栏一张卡的 MAA 同源低层观测。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    region: PixelRegionV1
    flag_score: float = Field(ge=0, le=1)
    cost: int | None = Field(default=None, ge=0)
    cost_confidence: float | None = Field(default=None, ge=0, le=1)
    cost_raw: str = ""
    available: bool | None = None
    availability_mean_v: float | None = Field(default=None, ge=0, le=255)
    cooling: bool | None = None
    cooling_pixels: int | None = Field(default=None, ge=0)
    identity: str | None = None
    identity_role: str | None = None
    identity_score: float | None = Field(default=None, ge=-1, le=1)
    identity_runner_up: str | None = None
    identity_runner_up_score: float | None = Field(default=None, ge=-1, le=1)


class DeploymentCardsReadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1)
    cards: tuple[DeploymentCardReadV1, ...]


class DeploymentCardsReader(Protocol):
    def __call__(
        self,
        image_path: Path,
        *,
        paused: bool | None,
    ) -> DeploymentCardsReadV1: ...


class BattlefieldOperatorReadV1(BaseModel):
    """One MAA health-bar detection mapped into the semantic stage grid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: PixelRegionV1
    detection_score: float = Field(ge=0, le=1)
    tile: TileCoordinateV1
    direction: str | None = None
    direction_score: float | None = Field(default=None, ge=0, le=1)
    direction_region: PixelRegionV1 | None = None
    direction_probabilities: tuple[float, float, float, float] | None = None
    skill_ready: bool | None = None
    skill_class: str | None = None
    skill_score: float | None = Field(default=None, ge=0, le=1)
    skill_region: PixelRegionV1 | None = None
    skill_probabilities: tuple[float, float, float] | None = None


class BattlefieldOperatorsReadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1)
    operators: tuple[BattlefieldOperatorReadV1, ...]


class BattlefieldOperatorsReader(Protocol):
    def __call__(
        self,
        image_path: Path,
        *,
        stage_grid: StageGrid,
    ) -> BattlefieldOperatorsReadV1: ...


class EnemyPressureReadV1(BaseModel):
    """低层红橙色敌方血条观测；只用于聚合压力，不声称识别敌人身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1)
    pressure: EnemyPressureV1
    regions: tuple[PixelRegionV1, ...]
    confidence: float = Field(ge=0, le=1)


class EnemyPressureReader(Protocol):
    def __call__(self, image_path: Path) -> EnemyPressureReadV1: ...


class RedHealthBarEnemyPressureReader:
    """从敌方受击后出现的红橙血条读取三路交战压力。

    该读数刻意不等价于“场上敌人总数”：未受击、隐形或血条被遮挡的敌人不会被计入。
    1-7 的红色出怪门位于画面最右侧，读取区在其左侧截止，避免静态红色门框假阳性。
    """

    BACKEND = "opencv-hsv-enemy-health-bars@1"

    def __call__(self, image_path: Path) -> EnemyPressureReadV1:
        import cv2  # type: ignore[import-not-found]

        image = _cv_imread(image_path)
        if image is None:
            raise ValueError(f"cannot decode enemy-pressure image: {image_path}")
        height, width = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (6, 150, 140), (12, 255, 255))

        # Canonical 1280x720 battlefield window. HUD, bottom cards and the red
        # spawn-gate decoration are excluded before connected-component work.
        top = int(round(100 * height / CANONICAL_HEIGHT))
        bottom = int(round(565 * height / CANONICAL_HEIGHT))
        left = int(round(100 * width / CANONICAL_WIDTH))
        right = int(round(1000 * width / CANONICAL_WIDTH))
        mask[:top, :] = 0
        mask[bottom:, :] = 0
        mask[:, :left] = 0
        mask[:, right:] = 0

        count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)
        sx, sy = width / CANONICAL_WIDTH, height / CANONICAL_HEIGHT
        regions: list[PixelRegionV1] = []
        center_xs: list[float] = []
        lanes = {"top": 0, "middle": 0, "bottom": 0}
        for index in range(1, count):
            x, y, bar_width, bar_height, area = (int(v) for v in stats[index])
            canonical_width = bar_width / sx
            canonical_height = bar_height / sy
            if not (
                15 <= canonical_width <= 100
                and 1 <= canonical_height <= 9
                and canonical_width / canonical_height >= 3.0
                and area / (sx * sy) >= 20
            ):
                continue
            center_x, center_y = (float(v) for v in centers[index])
            regions.append(
                PixelRegionV1(x=x, y=y, width=bar_width, height=bar_height)
            )
            center_xs.append(center_x)
            canonical_y = center_y / sy
            if canonical_y < 260:
                lanes["top"] += 1
            elif canonical_y < 420:
                lanes["middle"] += 1
            else:
                lanes["bottom"] += 1

        visible = len(regions)
        # Shape and saturation gates are deterministic; confidence states the
        # detector gate, not a learned enemy identity probability.
        confidence = 0.92 if visible else 0.90
        return EnemyPressureReadV1(
            backend=self.BACKEND,
            pressure=EnemyPressureV1(
                visible_health_bars=visible,
                top_lane=lanes["top"],
                middle_lane=lanes["middle"],
                bottom_lane=lanes["bottom"],
                closest_screen_x=min(center_xs) if center_xs else None,
            ),
            regions=tuple(regions),
            confidence=confidence,
        )


class RapidOCRBattleReader:
    """通过项目统一识别服务读取冻结帧 ROI。"""

    # These glyph crops were calibrated against 219 stratified frames from
    # the official-server chapter progression runs.  They deliberately avoid
    # the decorative HUD marks that made full-frame OCR append a stray digit.
    _FIXED_ROIS = {
        "kills": SourcePixelRect(x=545, y=4, width=95, height=55),
        "life": SourcePixelRect(x=746, y=3, width=36, height=58),
        "cost": SourcePixelRect(x=1208, y=490, width=62, height=50),
    }
    _FIXED_THRESHOLDS = {
        "kills": 0.70,
        "life": 0.70,
        "cost": 0.85,
    }
    _FIXED_BACKEND = "rapidocr_onnxruntime+fixed-roi-rec-only@1"
    _HEAVY_FALLBACK_SUFFIX = "+fixed-roi-heavy-fallback"

    def __init__(self, service: RecognitionService | None = None) -> None:
        self._service = service or RecognitionService()
        self._fixed_roi_engine: Any | None = None

    def __call__(self, image_path: Path, region: SourcePixelRect) -> OCRRead:
        result = self._service.ocr_region(
            image_path,
            region,
            backend="rapidocr_onnxruntime",
        )
        return OCRRead(
            backend=f"{result.backend}@{result.backend_version}",
            tokens=tuple(
                OCRToken(text=item.text or "", confidence=item.score)
                for item in result.detections
                if item.text
            ),
        )

    def read_regions(
        self,
        image_path: Path,
        regions: dict[str, SourcePixelRect],
    ) -> tuple[dict[str, OCRRead], dict[str, str | None]]:
        """Read the small HUD glyphs first and fail closed to focused OCR.

        Full-frame RapidOCR ran detection, classification, and recognition on
        every paused 1280x720 frame.  Historical profiling showed that this
        accounted for most of the 17-second perception gap.  The common path
        now performs recognition only on three calibrated crops.  A crop that
        is malformed or below its independently calibrated confidence gate is
        retried with the existing detector-backed ROI reader.
        """

        image = _cv_imread(image_path)
        reads: dict[str, OCRRead] = {}
        errors: dict[str, str | None] = {name: None for name in regions}
        for name, requested_region in regions.items():
            if name == "pause" and image is not None:
                reads[name] = OCRRead(
                    backend="opencv-pause-overlay-geometry@1",
                    tokens=(
                        (OCRToken(text="PAUSE", confidence=0.99),)
                        if self._pause_overlay_visible(image)
                        else ()
                    ),
                )
                continue

            fast_region = self._FIXED_ROIS.get(name)
            if image is not None and fast_region is not None:
                try:
                    fast_read = self._read_fixed_roi(image, fast_region)
                except Exception:  # noqa: BLE001 - focused heavy OCR remains available
                    fast_read = OCRRead(backend=self._FIXED_BACKEND)
                if self._fixed_read_is_accepted(name, fast_read):
                    reads[name] = fast_read
                    continue

            try:
                heavy_read = self(image_path, requested_region)
                reads[name] = heavy_read.model_copy(
                    update={
                        "backend": (
                            f"{heavy_read.backend}{self._HEAVY_FALLBACK_SUFFIX}"
                        )
                    }
                )
            except Exception as exc:  # noqa: BLE001 - single-field fail closed
                reads[name] = OCRRead(backend="ocr-error")
                errors[name] = f"{type(exc).__name__}: {exc}"
        return reads, errors

    def _read_fixed_roi(self, image: Any, region: SourcePixelRect) -> OCRRead:
        import cv2  # type: ignore[import-not-found]
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

        if self._fixed_roi_engine is None:
            self._fixed_roi_engine = RapidOCR(
                use_det=False,
                use_cls=False,
                use_rec=True,
            )
        height, width = image.shape[:2]
        scaled = _scale_region(region, width, height)
        crop = image[
            scaled.y : scaled.y + scaled.height,
            scaled.x : scaled.x + scaled.width,
        ]
        if crop.size == 0:
            return OCRRead(backend=self._FIXED_BACKEND)
        enlarged = cv2.resize(
            crop,
            None,
            fx=6.0,
            fy=6.0,
            interpolation=cv2.INTER_CUBIC,
        )
        result, _timings = self._fixed_roi_engine(
            enlarged,
            use_det=False,
            use_cls=False,
            use_rec=True,
        )
        tokens = tuple(
            OCRToken(text=str(item[0]), confidence=float(item[1]))
            for item in (result or ())
            if len(item) >= 2 and str(item[0]).strip()
        )
        return OCRRead(backend=self._FIXED_BACKEND, tokens=tokens)

    @classmethod
    def _fixed_read_is_accepted(cls, name: str, read: OCRRead) -> bool:
        threshold = cls._FIXED_THRESHOLDS[name]
        candidates = [
            token
            for token in read.tokens
            if token.confidence >= threshold
        ]
        if len(candidates) != 1:
            return False
        text = candidates[0].text.strip()
        if name == "kills":
            match = _KILLS.fullmatch(text)
            return bool(match and int(match.group(1)) <= int(match.group(2)))
        return bool(_INTEGER.fullmatch(text))

    @staticmethod
    def _pause_overlay_visible(image: Any) -> bool:
        import cv2  # type: ignore[import-not-found]

        height, width = image.shape[:2]
        x, y, roi_width, roi_height = 470, 270, 340, 140
        left = round(x * width / CANONICAL_WIDTH)
        top = round(y * height / CANONICAL_HEIGHT)
        right = round((x + roi_width) * width / CANONICAL_WIDTH)
        bottom = round((y + roi_height) * height / CANONICAL_HEIGHT)
        roi = image[top:bottom, left:right]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 140)).astype("uint8")
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask,
            8,
        )
        x_scale = width / CANONICAL_WIDTH
        y_scale = height / CANONICAL_HEIGHT
        area_scale = x_scale * y_scale
        glyphs = [
            (int(glyph_left), int(glyph_top), int(glyph_width), int(glyph_height))
            for glyph_left, glyph_top, glyph_width, glyph_height, area in stats[1:]
            if area >= 700 * area_scale
            and 30 * x_scale <= glyph_width <= 65 * x_scale
            and 55 * y_scale <= glyph_height <= 90 * y_scale
            and 10 * y_scale <= glyph_top <= 45 * y_scale
        ]
        glyphs.sort()
        for index in range(len(glyphs) - 4):
            word = glyphs[index : index + 5]
            tops = [glyph[1] for glyph in word]
            heights = [glyph[3] for glyph in word]
            left_gaps = [
                word[position + 1][0] - word[position][0]
                for position in range(4)
            ]
            span = word[-1][0] + word[-1][2] - word[0][0]
            if (
                max(tops) - min(tops) <= 8 * y_scale
                and max(heights) - min(heights) <= 12 * y_scale
                and all(42 * x_scale <= gap <= 78 * x_scale for gap in left_gaps)
                and 240 * x_scale <= span <= 310 * x_scale
            ):
                return True
        return False


class DeploymentCardReader:
    """按 MAA ``BattlefieldMatcher::deployment_analyze`` 真源解析底栏。

    MAA 的流程是：``BattleOpersFlag`` 模板分卡，``BattleOperCost`` 字符 OCR，
    ``BattleOperAvailable`` 的 HSV-V 均值判可用，``BattleOperCooling`` 的 HSV 像素数判冷却。
    本实现动态读取任务参数；暂停压暗会污染后两项，因此暂停帧只报告卡片与费用，状态降为未知。
    """

    def __init__(self, maa_root: Path | str | None = None) -> None:
        from .maa_operator import MaaPaths

        paths = MaaPaths.locate(root=maa_root)
        root = paths.root
        tasks_path = root / "resource" / "tasks" / "tasks.json"
        self._tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        self._template_path = (
            root
            / "resource"
            / "template"
            / "Battle"
            / "BattleFlag"
            / "BattleOpersFlag.png"
        )
        if not self._template_path.is_file():
            raise FileNotFoundError(f"MAA deployment flag template missing: {self._template_path}")
        battle_data = json.loads(
            (root / "resource" / "battle_data.json").read_text(encoding="utf-8")
        )
        chars = battle_data.get("chars", {})
        self._avatar_sources: tuple[tuple[Path, str, str], ...] = tuple(
            (
                avatar_path,
                # MaaCore's BattleDataConfig and copilot actions use ``name`` as
                # the canonical operator identity. ``name_en`` is display
                # metadata and cannot be compared directly with Chinese action
                # names such as “芬”.
                str(chars[avatar_path.stem]["name"]),
                str(chars[avatar_path.stem]["profession"]),
            )
            for avatar_path in sorted((paths.user_dir / "cache" / "avatars").glob("*.png"))
            if avatar_path.stem in chars
        )
        self._avatar_templates: tuple[tuple[Any, str, str], ...] | None = None
        self._ocr_engine: Any | None = None
        self._cost_ocr_cache: dict[
            bytes,
            tuple[int | None, float | None, str],
        ] = {}

    @staticmethod
    def _move_rect(base: tuple[int, int, int, int], move: list[int]) -> tuple[int, int, int, int]:
        return base[0] + move[0], base[1] + move[1], move[2], move[3]

    @staticmethod
    def _clip_rect(
        rect: tuple[int, int, int, int],
        width: int,
        height: int,
    ) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    def _cost_ocr(self, image: Any, rect: tuple[int, int, int, int]) -> tuple[int | None, float | None, str]:
        import cv2  # type: ignore[import-not-found]

        x, y, w, h = self._clip_rect(rect, CANONICAL_WIDTH, CANONICAL_HEIGHT)
        if w <= 0 or h <= 0:
            return None, None, ""
        crop = image[y : y + h, x : x + w]
        fingerprint = hashlib.sha256(crop.tobytes()).digest()
        if fingerprint in self._cost_ocr_cache:
            return self._cost_ocr_cache[fingerprint]
        crop = cv2.resize(crop, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
        if self._ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

            self._ocr_engine = RapidOCR()
        raw, _elapsed = self._ocr_engine(
            crop,
            use_det=False,
            use_cls=False,
            use_rec=True,
        )
        candidates: list[tuple[str, float]] = []
        for item in raw or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            text = str(item[0]).strip()
            try:
                confidence = float(item[1])
            except (TypeError, ValueError):
                continue
            if _INTEGER.fullmatch(text) and confidence >= 0.75:
                candidates.append((text, confidence))
        values = {text for text, _ in candidates}
        if len(values) != 1:
            raw_text = "|".join(str(item[0]) for item in (raw or []) if item)
            result = (None, None, raw_text)
        else:
            winner = max(candidates, key=lambda item: item[1])
            result = (int(winner[0]), winner[1], winner[0])
        if len(self._cost_ocr_cache) >= 128:
            self._cost_ocr_cache.pop(next(iter(self._cost_ocr_cache)))
        self._cost_ocr_cache[fingerprint] = result
        return result

    def _load_avatar_templates(self) -> tuple[tuple[Any, str, str], ...]:
        import cv2  # type: ignore[import-not-found]

        if self._avatar_templates is None:
            templates: list[tuple[Any, str, str]] = []
            for path, name, role in self._avatar_sources:
                image = _cv_imread(path)
                if image is not None and image.shape[:2] == (60, 60):
                    templates.append((image, name, role))
            self._avatar_templates = tuple(templates)
        return self._avatar_templates

    def _match_avatar(
        self,
        image: Any,
        rect: tuple[int, int, int, int],
    ) -> tuple[str | None, str | None, float | None, str | None, float | None]:
        """Match one card avatar using MAA's cache and fail closed on ambiguity."""
        import cv2  # type: ignore[import-not-found]

        x, y, w, h = self._clip_rect(rect, CANONICAL_WIDTH, CANONICAL_HEIGHT)
        if (w, h) != (60, 60):
            return None, None, None, None, None
        crop = image[y : y + h, x : x + w]
        scores = sorted(
            (
                (
                    float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0, 0]),
                    name,
                    role,
                )
                for template, name, role in self._load_avatar_templates()
            ),
            reverse=True,
        )
        if not scores:
            return None, None, None, None, None
        best_score, best_name, best_role = scores[0]
        runner_score, runner_name, _runner_role = (
            scores[1] if len(scores) > 1 else (-1.0, "", "")
        )
        # MAA's BattleAvatarData threshold is 0.8. The additional separation
        # gate prevents a near tie from becoming a fabricated identity.
        if best_score < 0.8 or best_score - runner_score < 0.08:
            return None, None, best_score, runner_name or None, runner_score
        return best_name, best_role, best_score, runner_name or None, runner_score

    def __call__(
        self,
        image_path: Path,
        *,
        paused: bool | None,
    ) -> DeploymentCardsReadV1:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        original = _cv_imread(image_path)
        if original is None:
            raise ValueError(f"cannot decode deployment image: {image_path}")
        actual_height, actual_width = original.shape[:2]
        image = cv2.resize(
            original,
            (CANONICAL_WIDTH, CANONICAL_HEIGHT),
            interpolation=cv2.INTER_AREA,
        )
        template = _cv_imread(self._template_path)
        if template is None:
            raise ValueError(f"cannot decode deployment template: {self._template_path}")

        flag_task = self._tasks["BattleOpersFlag"]
        flag_roi = tuple(int(value) for value in flag_task["roi"])
        x, y, w, h = flag_roi
        search = image[y : y + h, x : x + w]
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        lower, upper = (int(value) for value in flag_task["maskRange"])
        mask = cv2.inRange(gray_template, lower, upper)
        response = cv2.matchTemplate(
            search,
            template,
            cv2.TM_CCOEFF_NORMED,
            mask=mask,
        )
        response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
        threshold = float(flag_task["templThreshold"])
        candidates = [
            (float(response[cy, cx]), x + int(cx), y + int(cy))
            for cy, cx in zip(*np.where(response >= threshold), strict=False)
        ]
        selected: list[tuple[float, int, int]] = []
        template_width = int(template.shape[1])
        for candidate in sorted(candidates, reverse=True):
            if all(abs(candidate[1] - old[1]) >= template_width for old in selected):
                selected.append(candidate)
        selected.sort(key=lambda item: item[1])

        click_move = self._tasks["BattleOperClickRange"]["rectMove"]
        available_move = self._tasks["BattleOperAvailable"]["rectMove"]
        available_threshold = float(self._tasks["BattleOperAvailable"]["specialParams"][0])
        cooling_task = self._tasks["BattleOperCooling"]
        cooling_move = cooling_task["rectMove"]
        cooling_threshold = int(cooling_task["specialParams"][0])
        cooling_lower, cooling_upper = cooling_task["colorScales"][0]
        cost_move = self._tasks["BattleOperCost"]["rectMove"]
        avatar_move = self._tasks["BattleOperAvatar"]["rectMove"]

        cards: list[DeploymentCardReadV1] = []
        for index, (score, flag_x, flag_y) in enumerate(selected):
            flag_rect = (flag_x, flag_y, template.shape[1], template.shape[0])
            card_rect = self._clip_rect(
                self._move_rect(flag_rect, click_move),
                CANONICAL_WIDTH,
                CANONICAL_HEIGHT,
            )
            cost_rect = self._move_rect(flag_rect, cost_move)
            cost, cost_confidence, cost_raw = self._cost_ocr(image, cost_rect)
            identity, identity_role, identity_score, runner_up, runner_up_score = (
                self._match_avatar(image, self._move_rect(card_rect, avatar_move))
            )

            available: bool | None = None
            mean_v: float | None = None
            cooling: bool | None = None
            cooling_pixels: int | None = None
            if paused is False:
                ax, ay, aw, ah = self._clip_rect(
                    self._move_rect(flag_rect, available_move),
                    CANONICAL_WIDTH,
                    CANONICAL_HEIGHT,
                )
                available_hsv = cv2.cvtColor(
                    image[ay : ay + ah, ax : ax + aw], cv2.COLOR_BGR2HSV
                )
                mean_v = float(cv2.mean(available_hsv)[2])
                available = mean_v >= available_threshold

                cx, cy, cw, ch = self._clip_rect(
                    self._move_rect(flag_rect, cooling_move),
                    CANONICAL_WIDTH,
                    CANONICAL_HEIGHT,
                )
                cooling_hsv = cv2.cvtColor(
                    image[cy : cy + ch, cx : cx + cw], cv2.COLOR_BGR2HSV
                )
                cooling_mask = cv2.inRange(
                    cooling_hsv,
                    tuple(int(value) for value in cooling_lower),
                    tuple(int(value) for value in cooling_upper),
                )
                cooling_pixels = int(cv2.countNonZero(cooling_mask))
                cooling = cooling_pixels >= cooling_threshold

            sx, sy = actual_width / CANONICAL_WIDTH, actual_height / CANONICAL_HEIGHT
            card_actual = PixelRegionV1(
                x=max(0, int(round(card_rect[0] * sx))),
                y=max(0, int(round(card_rect[1] * sy))),
                width=max(1, int(round(card_rect[2] * sx))),
                height=max(1, int(round(card_rect[3] * sy))),
            )
            cards.append(
                DeploymentCardReadV1(
                    index=index,
                    region=card_actual,
                    flag_score=score,
                    cost=cost,
                    cost_confidence=cost_confidence,
                    cost_raw=cost_raw,
                    available=available,
                    availability_mean_v=mean_v,
                    cooling=cooling,
                    cooling_pixels=cooling_pixels,
                    identity=identity,
                    identity_role=identity_role,
                    identity_score=identity_score,
                    identity_runner_up=runner_up,
                    identity_runner_up_score=runner_up_score,
                )
            )
        return DeploymentCardsReadV1(
            backend="maa-battlefield-matcher-compatible+rapidocr_onnxruntime",
            cards=tuple(cards),
        )


class MaaBattlefieldOperatorReader:
    """Run MAA's operator health-bar detector and map boxes to stage tiles.

    The preprocessing, 0.3 candidate gate, asymmetric NMS, and
    ``BattleOperBoxRectMove`` containment mapping follow MAA's source. A
    stricter 0.5 reporting gate rejects a frozen pause-overlay false positive.
    """

    def __init__(self, maa_root: Path | str | None = None) -> None:
        from .maa_operator import MaaPaths

        root = MaaPaths.locate(root=maa_root).root
        model_dir = root / "resource" / "onnx"
        self._model_paths = {
            "operators_det": model_dir / "operators_det.onnx",
            "deploy_direction_cls": model_dir / "deploy_direction_cls.onnx",
            "skill_ready_cls": model_dir / "skill_ready_cls.onnx",
        }
        missing = [path for path in self._model_paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"MAA battlefield models missing: {missing}")
        tasks = json.loads(
            (root / "resource" / "tasks" / "tasks.json").read_text(
                encoding="utf-8"
            )
        )
        self._tile_box_move = tuple(
            int(value) for value in tasks["BattleOperBoxRectMove"]["rectMove"]
        )
        self._direction_move = tuple(
            int(value)
            for value in tasks["BattleDeployDirectionRectMove"]["rectMove"]
        )
        self._skill_move = tuple(
            int(value) for value in tasks["BattleSkillReady"]["rectMove"]
        )
        self._sessions: dict[str, Any] = {}

    REPORTING_THRESHOLD = 0.5
    CLASSIFICATION_THRESHOLD = 0.9

    def _get_session(self, name: str) -> Any:
        if name not in self._sessions:
            import onnxruntime as ort  # type: ignore[import-not-found]

            self._sessions[name] = ort.InferenceSession(
                str(self._model_paths[name]),
                providers=["CPUExecutionProvider"],
            )
        return self._sessions[name]

    @staticmethod
    def _intersection_area(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> int:
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[0] + first[2], second[0] + second[2])
        bottom = min(first[1] + first[3], second[1] + second[3])
        return max(0, right - left) * max(0, bottom - top)

    @classmethod
    def _nms(
        cls,
        boxes: list[tuple[float, tuple[int, int, int, int]]],
    ) -> list[tuple[float, tuple[int, int, int, int]]]:
        # MAA suppresses a lower-score box when 70% of that box is covered;
        # this intentionally is not conventional IoU NMS.
        ranked = sorted(boxes, reverse=True)
        kept: list[tuple[float, tuple[int, int, int, int]]] = []
        for score, box in ranked:
            if score < 0.1:
                continue
            if any(
                cls._intersection_area(old_box, box) > 0.7 * box[2] * box[3]
                for _old_score, old_box in kept
            ):
                continue
            kept.append((score, box))
        return kept

    @classmethod
    def _reporting_candidates(
        cls,
        boxes: list[tuple[float, tuple[int, int, int, int]]],
    ) -> list[tuple[float, tuple[int, int, int, int]]]:
        return [
            item
            for item in cls._nms(boxes)
            if item[0] >= cls.REPORTING_THRESHOLD
        ]

    @staticmethod
    def _softmax(values: Any) -> Any:
        import numpy as np

        array = np.asarray(values, dtype=np.float32)
        exponent = np.exp(array - np.max(array))
        return exponent / np.sum(exponent)

    @staticmethod
    def _tensor(image: Any, *, imagenet_normalize: bool = False) -> Any:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        tensor = (
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            .transpose(2, 0, 1)
            .astype(np.float32)
            / 255.0
        )
        if imagenet_normalize:
            mean = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)[:, None, None]
            std = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)[:, None, None]
            tensor = (tensor - mean) / std
        return tensor[None, ...]

    @staticmethod
    def _exact_crop(
        image: Any,
        base_x: int,
        base_y: int,
        move: tuple[int, int, int, int],
    ) -> tuple[Any | None, PixelRegionV1 | None]:
        x, y = base_x + move[0], base_y + move[1]
        width, height = move[2], move[3]
        if x < 0 or y < 0 or x + width > image.shape[1] or y + height > image.shape[0]:
            return None, None
        return (
            image[y : y + height, x : x + width],
            PixelRegionV1(x=x, y=y, width=width, height=height),
        )

    def _classify_tile(
        self,
        image: Any,
        base_x: int,
        base_y: int,
    ) -> dict[str, Any]:
        import cv2  # type: ignore[import-not-found]

        result: dict[str, Any] = {}
        direction_crop, direction_region = self._exact_crop(
            image, base_x, base_y, self._direction_move
        )
        if direction_crop is not None:
            session = self._get_session("deploy_direction_cls")
            raw = session.run(
                None,
                {session.get_inputs()[0].name: self._tensor(direction_crop)},
            )[0][0]
            probabilities = self._softmax(raw)
            class_id = int(probabilities.argmax())
            score = float(probabilities[class_id])
            result.update(
                direction=(
                    ("Right", "Down", "Left", "Up")[class_id]
                    if score >= self.CLASSIFICATION_THRESHOLD
                    else None
                ),
                direction_score=score,
                direction_region=direction_region,
                direction_probabilities=tuple(float(value) for value in probabilities),
            )

        skill_crop, skill_region = self._exact_crop(
            image, base_x, base_y, self._skill_move
        )
        if skill_crop is not None:
            resized = cv2.resize(
                skill_crop,
                (72, 72),
                interpolation=cv2.INTER_CUBIC,
            )[4:68, 4:68]
            session = self._get_session("skill_ready_cls")
            raw = session.run(
                None,
                {
                    session.get_inputs()[0].name: self._tensor(
                        resized,
                        imagenet_normalize=True,
                    )
                },
            )[0][0]
            probabilities = self._softmax(raw)
            class_id = int(probabilities.argmax())
            score = float(probabilities[class_id])
            skill_class = ("covered", "not_ready", "ready")[class_id]
            ready: bool | None = None
            if score >= self.CLASSIFICATION_THRESHOLD and class_id in (1, 2):
                ready = class_id == 2
            result.update(
                skill_ready=ready,
                skill_class=skill_class,
                skill_score=score,
                skill_region=skill_region,
                skill_probabilities=tuple(float(value) for value in probabilities),
            )
        return result

    def __call__(
        self,
        image_path: Path,
        *,
        stage_grid: StageGrid,
    ) -> BattlefieldOperatorsReadV1:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        image = _cv_imread(image_path)
        if image is None:
            raise ValueError(f"cannot decode battlefield image: {image_path}")
        height, width = image.shape[:2]
        resized = cv2.resize(image, (640, 640), interpolation=cv2.INTER_AREA)
        tensor = (
            cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            .transpose(2, 0, 1)
            .astype(np.float32)
            / 255.0
        )[None, ...]
        session = self._get_session("operators_det")
        output = session.run(
            None,
            {session.get_inputs()[0].name: tensor},
        )[0][0]
        candidates: list[tuple[float, tuple[int, int, int, int]]] = []
        x_scale, y_scale = 640.0 / width, 640.0 / height
        for index in np.where(output[4] >= 0.3)[0]:
            score = float(output[4, index])
            center_x = int(float(output[0, index]) / x_scale)
            center_y = int(float(output[1, index]) / y_scale)
            box_width = int(float(output[2, index]) / x_scale)
            box_height = int(float(output[3, index]) / y_scale)
            candidates.append(
                (
                    score,
                    (
                        center_x - box_width // 2,
                        center_y - box_height // 2,
                        box_width,
                        box_height,
                    ),
                )
            )

        projector = TileProjector(width, height, stage_grid)
        projected = projector.project_all()
        move_x, move_y, move_width, move_height = self._tile_box_move
        by_tile: dict[tuple[int, int], BattlefieldOperatorReadV1] = {}
        for score, box in self._reporting_candidates(candidates):
            map_x, map_y = box[0] + move_x, box[1] + move_y
            matches = [
                point
                for point in projected
                if map_x <= point.x < map_x + move_width
                and map_y <= point.y < map_y + move_height
            ]
            if len(matches) != 1:
                continue
            point = matches[0]
            classification = self._classify_tile(
                image,
                int(point.x),
                int(point.y),
            )
            region = PixelRegionV1(
                x=max(0, box[0]),
                y=max(0, box[1]),
                width=max(1, min(width, box[0] + box[2]) - max(0, box[0])),
                height=max(1, min(height, box[1] + box[3]) - max(0, box[1])),
            )
            observation = BattlefieldOperatorReadV1(
                region=region,
                detection_score=score,
                tile=TileCoordinateV1(col=point.col, row=point.row),
                **classification,
            )
            old = by_tile.get(point.tile)
            if old is None or observation.detection_score > old.detection_score:
                by_tile[point.tile] = observation
        return BattlefieldOperatorsReadV1(
            backend="maa-operators_det.onnx@source-compatible+validated-0.5-gate",
            operators=tuple(
                by_tile[key]
                for key in sorted(by_tile, key=lambda item: (item[1], item[0]))
            ),
        )


def _scale_region(region: SourcePixelRect, width: int, height: int) -> SourcePixelRect:
    sx = width / CANONICAL_WIDTH
    sy = height / CANONICAL_HEIGHT
    x = int(round(region.x * sx))
    y = int(round(region.y * sy))
    scaled_width = max(1, int(round(region.width * sx)))
    scaled_height = max(1, int(round(region.height * sy)))
    return SourcePixelRect(
        x=min(x, width - 1),
        y=min(y, height - 1),
        width=min(scaled_width, width - min(x, width - 1)),
        height=min(scaled_height, height - min(y, height - 1)),
    )


def _evidence(
    read: OCRRead,
    region: SourcePixelRect,
    *,
    confidence: float | None = None,
) -> FieldEvidenceV1:
    return FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend=read.backend,
        confidence=confidence,
        region=PixelRegionV1(**region.model_dump()),
        raw_text=tuple(item.text for item in read.tokens),
    )


def _unknown(reason: str, evidence: FieldEvidenceV1 | None = None) -> ObservedV1[Any]:
    return ObservedV1(
        known=False,
        evidence=evidence
        or FieldEvidenceV1(channel=SourceChannel.UNKNOWN, backend="not-observed"),
        reason=reason,
    )


def _known(value: T, evidence: FieldEvidenceV1) -> ObservedV1[T]:
    return ObservedV1(known=True, value=value, evidence=evidence)


def _parse_cost_progress(
    image_path: Path,
    *,
    width: int,
    height: int,
    cost_read: OCRRead,
    battle_hud_known: bool,
) -> ObservedV1[float]:
    """Read the white cooldown bar beneath current DP independently of the digits."""

    region = _scale_region(COST_PROGRESS_REGION, width, height)
    raw_text = tuple(item.text for item in cost_read.tokens)
    evidence = FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend="opencv-hsv-dp-cooldown-bar@1",
        region=PixelRegionV1(**region.model_dump()),
        raw_text=raw_text,
    )
    if not battle_hud_known:
        return _unknown("battle HUD is not fully confirmed", evidence)
    if any(item.strip().upper() == "MAX" for item in raw_text):
        return _known(
            1.0,
            evidence.model_copy(
                update={
                    "confidence": 0.99,
                    "raw_text": (*raw_text, "mode=capped"),
                }
            ),
        )

    import cv2  # type: ignore[import-not-found]
    import numpy as np

    image = _cv_imread(image_path)
    if image is None:
        return _unknown("battle frame could not be decoded", evidence)
    roi = image[
        region.y : region.y + region.height,
        region.x : region.x + region.width,
    ]
    if roi.size == 0:
        return _unknown("cost progress region is empty", evidence)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 150), (179, 100, 255))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(white)
    minimum_height = max(2, round(region.height * 0.6))
    components = [
        (
            int(x),
            int(y),
            int(component_width),
            int(component_height),
            int(area),
        )
        for x, y, component_width, component_height, area in stats[1:count]
    ]
    # When DP reaches its in-battle cap, the game replaces the left-anchored
    # fill bar with a centered MAX label.  OCR can miss the small seven-pixel
    # glyphs, but their joined white slice is spatially distinct from a fill
    # bar.  Treat the marker as a semantic cap observation, not as elapsed fill.
    cap_candidates = [
        item
        for item in components
        if round(region.width * 0.25) <= item[0] <= round(region.width * 0.55)
        and round(region.width * 0.18) <= item[2] <= round(region.width * 0.40)
        and item[3] >= minimum_height
        and item[4] / max(1, item[2] * item[3]) >= 0.55
    ]
    if cap_candidates:
        marker = max(cap_candidates, key=lambda item: (item[4], item[2]))
        return _known(
            1.0,
            evidence.model_copy(
                update={
                    "confidence": 0.95,
                    "raw_text": (
                        *raw_text,
                        "MAX",
                        "mode=capped",
                        f"marker={marker[0]},{marker[1]},{marker[2]},{marker[3]}",
                    ),
                }
            ),
        )
    candidates: list[tuple[int, int, int, int, int]] = []
    for x, y, component_width, component_height, area in components:
        if x <= 2 and component_height >= minimum_height:
            candidates.append(
                (
                    int(x),
                    int(y),
                    int(component_width),
                    int(component_height),
                    int(area),
                )
            )
    if not candidates:
        return _known(
            0.0,
            evidence.model_copy(
                update={"confidence": 0.9, "raw_text": (*raw_text, "bar_pixels=0")}
            ),
        )
    _x, _y, filled_width, component_height, area = max(
        candidates,
        key=lambda item: (item[2], item[4]),
    )
    density = area / max(1, filled_width * component_height)
    if density < 0.65:
        return _unknown("cost progress bar was not sufficiently solid", evidence)
    progress = min(1.0, filled_width / region.width)
    return _known(
        round(progress, 4),
        evidence.model_copy(
            update={
                "confidence": min(0.99, density),
                "raw_text": (*raw_text, f"filled={filled_width}/{region.width}"),
            }
        ),
    )


def _reconcile_capped_cost(
    cost: ObservedV1[int],
    cost_progress: ObservedV1[float],
) -> ObservedV1[int]:
    """Use the explicit MAX marker to recover the in-battle DP cap safely."""

    if "mode=capped" not in cost_progress.evidence.raw_text:
        return cost
    cap_evidence = cost_progress.evidence.model_copy(
        update={
            "backend": f"{cost_progress.evidence.backend}+arknights-dp-cap-99",
            "raw_text": (*cost_progress.evidence.raw_text, "cost=99"),
        }
    )
    if cost.known and cost.value is not None:
        if int(cost.value) == 99:
            return cost
        return _unknown(
            f"numeric cost {cost.value} conflicts with the visual MAX marker",
            cap_evidence,
        )
    return _known(99, cap_evidence)


def _read_region(
    reader: RegionOCR,
    image_path: Path,
    region: SourcePixelRect,
) -> tuple[OCRRead, str | None]:
    try:
        return reader(image_path, region), None
    except Exception as exc:  # noqa: BLE001 - 单字段失败必须降级为 unknown
        return OCRRead(backend="ocr-error"), f"{type(exc).__name__}: {exc}"


def _parse_integer(
    read: OCRRead,
    region: SourcePixelRect,
    *,
    threshold: float,
    error: str | None,
) -> ObservedV1[int]:
    evidence = _evidence(read, region)
    if error:
        return _unknown(f"OCR failed: {error}", evidence)
    matches = [
        item
        for item in read.tokens
        if item.confidence >= threshold and _INTEGER.fullmatch(item.text.strip())
    ]
    distinct = {item.text.strip() for item in matches}
    if len(distinct) != 1:
        return _unknown("numeric OCR did not yield exactly one unambiguous value", evidence)
    winner = max(matches, key=lambda item: item.confidence)
    return _known(
        int(winner.text.strip()),
        _evidence(read, region, confidence=winner.confidence),
    )


def _focused_integer_fallback(
    reader: RegionOCR,
    image_path: Path,
    region: SourcePixelRect,
    batch_read: OCRRead,
    *,
    threshold: float,
) -> ObservedV1[int]:
    """Retry a tactical integer on its ROI without hiding OCR disagreement.

    RapidOCR's full-frame detector can find a small DP glyph but assign it a
    confidence just below the tactical gate.  A focused crop is much more
    stable.  Any numeric value seen by the full-frame pass is therefore kept
    as a veto: the focused result is accepted only when the two passes agree,
    or when the full-frame pass saw no integer at all.
    """

    focused_read, focused_error = _read_region(reader, image_path, region)
    focused = _parse_integer(
        focused_read,
        region,
        threshold=threshold,
        error=focused_error,
    )
    if not focused.known:
        return focused
    batch_values = {
        int(item.text.strip())
        for item in batch_read.tokens
        if _INTEGER.fullmatch(item.text.strip())
    }
    if batch_values and batch_values != {focused.value}:
        return _unknown(
            "focused numeric OCR conflicts with full-frame numeric evidence",
            _evidence(batch_read, region),
        )
    evidence = focused.evidence.model_copy(
        update={"backend": f"{focused.evidence.backend}+focused-cost-fallback"}
    )
    return _known(focused.value, evidence)


def _parse_kills(
    read: OCRRead,
    region: SourcePixelRect,
    error: str | None,
    *,
    threshold: float = 0.90,
) -> tuple[ObservedV1[int], ObservedV1[int]]:
    evidence = _evidence(read, region)
    if error:
        reason = f"OCR failed: {error}"
        return _unknown(reason, evidence), _unknown(reason, evidence)
    matches: list[tuple[OCRToken, re.Match[str]]] = []
    for token in read.tokens:
        match = _KILLS.fullmatch(token.text.strip())
        if token.confidence >= threshold and match:
            matches.append((token, match))
    values = {(int(match.group(1)), int(match.group(2))) for _, match in matches}
    if len(values) != 1:
        reason = "kill OCR did not yield exactly one unambiguous current/total pair"
        return _unknown(reason, evidence), _unknown(reason, evidence)
    current, total = values.pop()
    if current > total:
        reason = "kill OCR current value exceeds total"
        return _unknown(reason, evidence), _unknown(reason, evidence)
    confidence = max(token.confidence for token, _ in matches)
    exact_evidence = _evidence(read, region, confidence=confidence)
    return _known(current, exact_evidence), _known(total, exact_evidence)


def _parse_paused(
    read: OCRRead,
    region: SourcePixelRect,
    error: str | None,
    *,
    battle_hud_known: bool,
) -> ObservedV1[bool]:
    evidence = _evidence(read, region)
    if error:
        return _unknown(f"OCR failed: {error}", evidence)
    matches = [
        item
        for item in read.tokens
        if item.confidence >= 0.85
        and ("PAUSE" in item.text.upper() or "暂停中" in item.text)
    ]
    if matches:
        winner = max(matches, key=lambda item: item.confidence)
        return _known(True, _evidence(read, region, confidence=winner.confidence))
    if battle_hud_known:
        # 在已确认是战斗 HUD 的帧上，冻结区域没有暂停标志，可判为未暂停。
        return _known(False, evidence)
    return _unknown(
        "pause marker is absent but the battle HUD itself is not fully confirmed",
        evidence,
    )


def _card_evidence(
    read: DeploymentCardsReadV1,
    card: DeploymentCardReadV1,
    *,
    confidence: float | None = None,
    raw_text: tuple[str, ...] = (),
) -> FieldEvidenceV1:
    return FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend=read.backend,
        confidence=confidence,
        region=card.region,
        raw_text=raw_text,
    )


def _deployed_from_reader(
    read: BattlefieldOperatorsReadV1 | None,
    error: str | None,
) -> ObservedV1[tuple[FriendlyOperatorV1, ...]]:
    if error:
        return _unknown(f"battlefield operator recognition failed: {error}")
    if read is None:
        return _unknown("stage grid was not supplied for battlefield operator mapping")

    operators: list[FriendlyOperatorV1] = []
    for operator in read.operators:
        evidence = FieldEvidenceV1(
            channel=SourceChannel.MAA_MODEL,
            backend=read.backend,
            confidence=operator.detection_score,
            region=operator.region,
            raw_text=(
                f"tile=({operator.tile.col},{operator.tile.row})",
            ),
        )
        identity_unobserved = FieldEvidenceV1(
            channel=SourceChannel.UNKNOWN,
            backend="battlefield-identity-not-observed",
            region=operator.region,
        )
        direction_evidence = FieldEvidenceV1(
            channel=SourceChannel.MAA_MODEL,
            backend=f"{read.backend}+deploy_direction_cls.onnx",
            confidence=operator.direction_score,
            region=operator.direction_region,
            raw_text=(
                ()
                if operator.direction_probabilities is None
                else tuple(
                    f"{name}={probability:.4f}"
                    for name, probability in zip(
                        ("Right", "Down", "Left", "Up"),
                        operator.direction_probabilities,
                        strict=True,
                    )
                )
            ),
        )
        skill_evidence = FieldEvidenceV1(
            channel=SourceChannel.MAA_MODEL,
            backend=f"{read.backend}+skill_ready_cls.onnx",
            confidence=operator.skill_score,
            region=operator.skill_region,
            raw_text=(
                ()
                if operator.skill_probabilities is None
                else tuple(
                    f"{name}={probability:.4f}"
                    for name, probability in zip(
                        ("covered", "not_ready", "ready"),
                        operator.skill_probabilities,
                        strict=True,
                    )
                )
            ),
        )
        operators.append(
            FriendlyOperatorV1(
                name=_unknown(
                    "the health-bar detector does not identify battlefield operators",
                    identity_unobserved,
                ),
                tile=_known(operator.tile, evidence),
                direction=(
                    _known(operator.direction, direction_evidence)
                    if operator.direction is not None
                    else _unknown(
                        "direction classifier did not clear the 0.9 reporting gate",
                        direction_evidence,
                    )
                ),
                skill_ready=(
                    _known(operator.skill_ready, skill_evidence)
                    if operator.skill_ready is not None
                    else _unknown(
                        (
                            "skill classifier reported an occluded crop"
                            if operator.skill_class == "covered"
                            else "skill classifier did not clear the 0.9 reporting gate"
                        ),
                        skill_evidence,
                    )
                ),
            )
        )

    if read.operators:
        left = min(item.region.x for item in read.operators)
        top = min(item.region.y for item in read.operators)
        right = max(item.region.x + item.region.width for item in read.operators)
        bottom = max(item.region.y + item.region.height for item in read.operators)
        group_region = PixelRegionV1(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        )
        confidence = min(item.detection_score for item in read.operators)
    else:
        group_region = None
        confidence = None
    return _known(
        tuple(operators),
        FieldEvidenceV1(
            channel=SourceChannel.MAA_MODEL,
            backend=read.backend,
            confidence=confidence,
            region=group_region,
        ),
    )


def _friendly_from_cards(
    read: DeploymentCardsReadV1 | None,
    error: str | None,
    *,
    paused: bool | None,
    deployed: ObservedV1[tuple[FriendlyOperatorV1, ...]],
) -> FriendlyBattleStateV1:
    if error:
        return FriendlyBattleStateV1(
            deployed=deployed,
            deployable=_unknown(f"deployment card recognition failed: {error}"),
        )
    if read is None or not read.cards:
        return FriendlyBattleStateV1(
            deployed=deployed,
            deployable=_unknown("no deployment cards were detected"),
        )

    operators: list[DeployableOperatorV1] = []
    for card in read.cards:
        flag_evidence = _card_evidence(read, card, confidence=card.flag_score)
        identity_evidence = _card_evidence(
            read,
            card,
            confidence=card.identity_score,
            raw_text=tuple(
                item
                for item in (
                    card.identity or None,
                    (
                        None
                        if card.identity_score is None
                        else f"best_score={card.identity_score:.4f}"
                    ),
                    (
                        None
                        if card.identity_runner_up is None
                        else f"runner_up={card.identity_runner_up}"
                    ),
                    (
                        None
                        if card.identity_runner_up_score is None
                        else f"runner_up_score={card.identity_runner_up_score:.4f}"
                    ),
                )
                if item is not None
            ),
        )
        cost_evidence = _card_evidence(
            read,
            card,
            confidence=card.cost_confidence,
            raw_text=(card.cost_raw,) if card.cost_raw else (),
        )
        if card.cost is None:
            cost = _unknown("card cost OCR was empty or ambiguous", cost_evidence)
        else:
            cost = _known(card.cost, cost_evidence)

        availability_evidence = _card_evidence(
            read,
            card,
            raw_text=(
                ()
                if card.availability_mean_v is None
                else (f"mean_v={card.availability_mean_v:.1f}",)
            ),
        )
        if card.available is None:
            reason = (
                "pause dimming contaminates the MAA brightness threshold"
                if paused is True
                else "card availability was not measured"
            )
            available = _unknown(reason, availability_evidence)
        else:
            available = _known(card.available, availability_evidence)

        cooling_evidence = _card_evidence(
            read,
            card,
            raw_text=(
                ()
                if card.cooling_pixels is None
                else (f"matching_pixels={card.cooling_pixels}",)
            ),
        )
        if card.cooling is None:
            reason = (
                "pause dimming contaminates the MAA cooling color threshold"
                if paused is True
                else "card cooling state was not measured"
            )
            cooling = _unknown(reason, cooling_evidence)
        else:
            cooling = _known(card.cooling, cooling_evidence)

        operators.append(
            DeployableOperatorV1(
                index=_known(card.index, flag_evidence),
                card_region=_known(card.region, flag_evidence),
                name=(
                    _known(card.identity, identity_evidence)
                    if card.identity is not None
                    else _unknown(
                        (
                            "avatar cache match did not clear the 0.8 score and "
                            "0.08 separation gates"
                            if card.identity_score is not None
                            else "no usable MAA avatar-cache templates were available"
                        ),
                        identity_evidence,
                    )
                ),
                cost=cost,
                available=available,
                cooling=cooling,
            )
        )

    left = min(card.region.x for card in read.cards)
    top = min(card.region.y for card in read.cards)
    right = max(card.region.x + card.region.width for card in read.cards)
    bottom = max(card.region.y + card.region.height for card in read.cards)
    group_evidence = FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend=read.backend,
        confidence=min(card.flag_score for card in read.cards),
        region=PixelRegionV1(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        ),
    )
    return FriendlyBattleStateV1(
        deployed=deployed,
        deployable=_known(tuple(operators), group_evidence),
    )


def _enemies_from_pressure(
    read: EnemyPressureReadV1 | None,
    error: str | None,
) -> EnemyBattleStateV1:
    units = _unknown(
        "enemy unit identity/health/status perception is not yet connected"
    )
    if error:
        return EnemyBattleStateV1(
            units=units,
            visible_pressure=_unknown(f"enemy pressure recognition failed: {error}"),
        )
    if read is None:
        return EnemyBattleStateV1(
            units=units,
            visible_pressure=_unknown("enemy pressure reader was not supplied"),
        )
    if read.regions:
        left = min(item.x for item in read.regions)
        top = min(item.y for item in read.regions)
        right = max(item.x + item.width for item in read.regions)
        bottom = max(item.y + item.height for item in read.regions)
        region = PixelRegionV1(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        )
    else:
        region = None
    evidence = FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend=read.backend,
        confidence=read.confidence,
        region=region,
        raw_text=tuple(
            f"bar={item.x},{item.y},{item.width},{item.height}"
            for item in read.regions
        ),
    )
    return EnemyBattleStateV1(
        units=units,
        visible_pressure=_known(read.pressure, evidence),
    )


def parse_battle_frame(
    image_path: Path | str,
    *,
    region_ocr: RegionOCR | None = None,
    deployment_cards_reader: DeploymentCardsReader | None = None,
    stage_grid: StageGrid | None = None,
    battlefield_operators_reader: BattlefieldOperatorsReader | None = None,
    enemy_pressure_reader: EnemyPressureReader | None = None,
    _include_tactical_readers: bool = True,
    _timings: dict[str, float] | None = None,
) -> BattleStateV1:
    """解析一张冻结帧；相同输入与识别器输出必得相同状态。"""
    total_started = time.perf_counter()
    frame_io_started = total_started
    path = Path(image_path).resolve()
    payload = path.read_bytes()
    with Image.open(path) as image:
        width, height = image.size
        image.verify()
    if _timings is not None:
        _timings["frame_io_seconds"] = time.perf_counter() - frame_io_started

    reader = region_ocr or RapidOCRBattleReader()
    regions = {
        "kills": _scale_region(KILLS_REGION, width, height),
        "life": _scale_region(LIFE_REGION, width, height),
        "cost": _scale_region(COST_REGION, width, height),
        "pause": _scale_region(PAUSE_REGION, width, height),
    }
    reads: dict[str, OCRRead] = {}
    errors: dict[str, str | None] = {}
    hud_started = time.perf_counter()
    batch_reader = getattr(reader, "read_regions", None)
    if callable(batch_reader):
        reads, errors = batch_reader(path, regions)
    else:
        for name, region in regions.items():
            reads[name], errors[name] = _read_region(reader, path, region)
    if _timings is not None:
        _timings["hud_read_seconds"] = time.perf_counter() - hud_started
        _timings["hud_heavy_fallback_count"] = float(
            sum(
                RapidOCRBattleReader._HEAVY_FALLBACK_SUFFIX in read.backend
                for read in reads.values()
            )
        )

    global_started = time.perf_counter()
    fast_kills = RapidOCRBattleReader._FIXED_BACKEND in reads["kills"].backend
    fast_life = RapidOCRBattleReader._FIXED_BACKEND in reads["life"].backend
    kills, total = _parse_kills(
        reads["kills"],
        regions["kills"],
        errors["kills"],
        threshold=(
            RapidOCRBattleReader._FIXED_THRESHOLDS["kills"]
            if fast_kills
            else 0.90
        ),
    )
    life = _parse_integer(
        reads["life"],
        regions["life"],
        threshold=(
            RapidOCRBattleReader._FIXED_THRESHOLDS["life"]
            if fast_life
            else 0.90
        ),
        error=errors["life"],
    )
    cost = _parse_integer(
        reads["cost"], regions["cost"], threshold=0.85, error=errors["cost"]
    )
    if (
        not cost.known
        and callable(batch_reader)
        and RapidOCRBattleReader._HEAVY_FALLBACK_SUFFIX
        not in reads["cost"].backend
    ):
        cost = _focused_integer_fallback(
            reader,
            path,
            regions["cost"],
            reads["cost"],
            threshold=0.85,
        )
    # Cost is a tactical field, but it is not a scene-identity anchor.  A single
    # low-confidence cost glyph must not suppress the independent card,
    # battlefield-operator, or enemy-pressure readers when kills/total/life
    # already prove that this is the live battle HUD.
    battle_scene_known = kills.known and total.known and life.known
    cost_progress = _parse_cost_progress(
        path,
        width=width,
        height=height,
        cost_read=reads["cost"],
        battle_hud_known=battle_scene_known,
    )
    cost = _reconcile_capped_cost(cost, cost_progress)
    paused = _parse_paused(
        reads["pause"],
        regions["pause"],
        errors["pause"],
        battle_hud_known=battle_scene_known,
    )
    if _timings is not None:
        _timings["global_parse_seconds"] = time.perf_counter() - global_started
    cards_read: DeploymentCardsReadV1 | None = None
    cards_error: str | None = None
    cards_started = time.perf_counter()
    if battle_scene_known and _include_tactical_readers:
        try:
            cards_reader = deployment_cards_reader or DeploymentCardReader()
            cards_read = cards_reader(
                path,
                paused=paused.value if paused.known else None,
            )
        except Exception as exc:  # noqa: BLE001 - 己方字段单独 fail closed
            cards_error = f"{type(exc).__name__}: {exc}"
    elif not battle_scene_known:
        cards_error = "battle HUD is not fully confirmed"
    else:
        cards_error = "deferred to the next bounded full tactical observation"
    if _timings is not None:
        _timings["deployment_cards_seconds"] = time.perf_counter() - cards_started
    battlefield_read: BattlefieldOperatorsReadV1 | None = None
    battlefield_error: str | None = None
    battlefield_started = time.perf_counter()
    if not battle_scene_known:
        battlefield_error = "battle HUD is not fully confirmed"
    elif not _include_tactical_readers:
        battlefield_error = "deferred to the next bounded full tactical observation"
    elif stage_grid is None:
        battlefield_error = None
    else:
        try:
            battlefield_reader = (
                battlefield_operators_reader or MaaBattlefieldOperatorReader()
            )
            battlefield_read = battlefield_reader(path, stage_grid=stage_grid)
        except Exception as exc:  # noqa: BLE001 - 场上己方字段单独 fail closed
            battlefield_error = f"{type(exc).__name__}: {exc}"
    if _timings is not None:
        _timings["battlefield_operators_seconds"] = (
            time.perf_counter() - battlefield_started
        )
    deployed = _deployed_from_reader(battlefield_read, battlefield_error)
    friendly = _friendly_from_cards(
        cards_read,
        cards_error,
        paused=paused.value if paused.known else None,
        deployed=deployed,
    )
    pressure_read: EnemyPressureReadV1 | None = None
    pressure_error: str | None = None
    pressure_started = time.perf_counter()
    if not battle_scene_known:
        pressure_error = "battle HUD is not fully confirmed"
    elif enemy_pressure_reader is not None:
        try:
            pressure_read = enemy_pressure_reader(path)
        except Exception as exc:  # noqa: BLE001 - 敌方压力字段单独 fail closed
            pressure_error = f"{type(exc).__name__}: {exc}"
    if _timings is not None:
        _timings["enemy_pressure_seconds"] = time.perf_counter() - pressure_started
    enemies = _enemies_from_pressure(pressure_read, pressure_error)
    state = BattleStateV1(
        frame=FrameReferenceV1(
            path=str(path),
            sha256=hashlib.sha256(payload).hexdigest(),
            width=width,
            height=height,
        ),
        friendly=friendly,
        enemies=enemies,
        global_state=GlobalBattleStateV1(
            current_cost=cost,
            cost_progress=cost_progress,
            kills=kills,
            total_enemies=total,
            life_points=life,
            paused=paused,
        ),
    )
    if _timings is not None:
        _timings["total_seconds"] = time.perf_counter() - total_started
    return state


class BattleStateParser:
    """复用 OCR、卡片模板与 ONNX 会话的实时解析器。

    ``parse_battle_frame`` 保持纯单帧入口；实时循环使用本对象，避免每帧重复载入模型。
    """

    def __init__(
        self,
        *,
        stage_grid: StageGrid,
        region_ocr: RegionOCR | None = None,
        deployment_cards_reader: DeploymentCardsReader | None = None,
        battlefield_operators_reader: BattlefieldOperatorsReader | None = None,
        enemy_pressure_reader: EnemyPressureReader | None = None,
        full_observation_interval: int = 1,
    ) -> None:
        if full_observation_interval < 1:
            raise ValueError("full_observation_interval must be at least 1")
        self._stage_grid = stage_grid
        self._region_ocr = region_ocr or RapidOCRBattleReader()
        self._deployment_cards_reader = (
            deployment_cards_reader or DeploymentCardReader()
        )
        self._battlefield_operators_reader = (
            battlefield_operators_reader or MaaBattlefieldOperatorReader()
        )
        self._enemy_pressure_reader = (
            enemy_pressure_reader or RedHealthBarEnemyPressureReader()
        )
        self.last_timings: dict[str, float] = {}
        self._timing_samples: list[dict[str, float]] = []
        self._full_observation_interval = full_observation_interval
        self._observation_count = 0
        self._force_full_observation = False

    def __call__(self, image_path: Path | str) -> BattleStateV1:
        timings: dict[str, float] = {}
        full_observation = (
            self._force_full_observation
            or self._observation_count % self._full_observation_interval == 0
        )
        self._force_full_observation = False
        self._observation_count += 1
        state = parse_battle_frame(
            image_path,
            region_ocr=self._region_ocr,
            deployment_cards_reader=self._deployment_cards_reader,
            stage_grid=self._stage_grid,
            battlefield_operators_reader=self._battlefield_operators_reader,
            enemy_pressure_reader=self._enemy_pressure_reader,
            _include_tactical_readers=full_observation,
            _timings=timings,
        )
        timings["full_tactical_observation"] = float(full_observation)
        self.last_timings = timings
        self._timing_samples.append(dict(timings))
        if len(self._timing_samples) > 256:
            del self._timing_samples[:-256]
        return _reconcile_stage_facility_card(state, self._stage_grid.code)

    def request_full_observation(self) -> None:
        """Force the next frame to include cards and battlefield operators."""

        self._force_full_observation = True

    def timing_summary(self) -> dict[str, Any]:
        """Return bounded live perception percentiles for the run receipt."""

        summary: dict[str, Any] = {"sample_count": len(self._timing_samples)}
        if not self._timing_samples:
            return summary
        fields = sorted(
            {key for sample in self._timing_samples for key in sample}
        )
        for field in fields:
            values = sorted(
                sample[field]
                for sample in self._timing_samples
                if field in sample
            )
            p50_index = max(0, (len(values) - 1) // 2)
            p90_index = max(0, min(len(values) - 1, (9 * len(values) + 9) // 10 - 1))
            summary[field] = {
                "mean": round(sum(values) / len(values), 4),
                "p50": round(values[p50_index], 4),
                "p90": round(values[p90_index], 4),
                "max": round(values[-1], 4),
            }
        return summary


def _reconcile_stage_facility_card(
    state: BattleStateV1,
    stage_code: str,
) -> BattleStateV1:
    """Name a facility only when its stage, visual cost and card position agree."""

    deployable = state.friendly.deployable
    if stage_code != "S2-3" or not deployable.known or deployable.value is None:
        return state
    cards = list(deployable.value)
    known_indices = [
        int(card.index.value)
        for card in cards
        if card.index.known and card.index.value is not None
    ]
    if len(known_indices) != len(cards) or not known_indices:
        return state
    rightmost_index = max(known_indices)
    candidates = [
        card
        for card in cards
        if not card.name.known
        and card.cost.known
        and card.cost.value == 5
        and card.index.known
        and card.index.value == rightmost_index
    ]
    if len(candidates) != 1:
        return state
    candidate = candidates[0]
    evidence = FieldEvidenceV1(
        channel=SourceChannel.CUSTOM,
        backend="stage-facility-card-registry@1",
        confidence=0.99,
        region=(
            candidate.card_region.value
            if candidate.card_region.known
            else candidate.name.evidence.region
        ),
        raw_text=(
            "stage=S2-3",
            "facility=trap_001_crate",
            "cost=5",
            "rightmost-card=true",
        ),
    )
    identified = candidate.model_copy(
        update={"name": _known(ROADBLOCK_NAME, evidence)}
    )
    cards[cards.index(candidate)] = identified
    reconciled_deployable = deployable.model_copy(update={"value": tuple(cards)})
    friendly = state.friendly.model_copy(
        update={"deployable": reconciled_deployable}
    )
    return state.model_copy(update={"friendly": friendly})


def battle_state_schema() -> dict[str, Any]:
    """返回可落盘的 JSON Schema。"""
    return BattleStateV1.model_json_schema(by_alias=True)
