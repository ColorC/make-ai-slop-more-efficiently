"""Single recognition authority for live Android and offline image analysis.

Business code submits typed requests here.  RapidOCR and MaaFramework remain replaceable
backends and are imported only inside their adapters, so callers cannot accidentally
create a second OCR authority.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import SourcePixelRect


RecognitionKind = Literal[
    "ocr",
    "template_match",
    "feature_match",
    "color_match",
    "pipeline",
]


class RecognitionRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(default="game-observatory.recognition-request.v1", alias="schema")
    kind: RecognitionKind
    image_path: str = Field(min_length=1)
    region: SourcePixelRect | None = None
    expected_text: tuple[str, ...] = ()
    template_paths: tuple[str, ...] = ()
    threshold: float = Field(default=0.3, ge=0, le=1)
    color_lower: tuple[tuple[int, int, int], ...] = ()
    color_upper: tuple[tuple[int, int, int], ...] = ()
    color_space: Literal["bgr", "hsv"] = "bgr"
    template_region: SourcePixelRect | None = None
    pipeline_entry: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_typed_inputs(self) -> "RecognitionRequestV1":
        if self.kind in {"template_match", "feature_match"} and not self.template_paths:
            raise ValueError(f"{self.kind} requires template_paths")
        if self.kind == "color_match" and (
            not self.color_lower or len(self.color_lower) != len(self.color_upper)
        ):
            raise ValueError("color_match requires paired lower and upper colors")
        if self.kind == "pipeline" and self.pipeline_entry is None:
            raise ValueError("pipeline recognition requires pipeline_entry")
        return self


class RecognitionDetectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = None
    score: float = Field(ge=0, le=1)
    bounds: SourcePixelRect | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class RecognitionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(default="game-observatory.recognition-result.v1", alias="schema")
    backend: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    kind: RecognitionKind
    hit: bool
    detections: tuple[RecognitionDetectionV1, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)


class RecognitionBackend(Protocol):
    name: str
    version: str

    def supports(self, kind: RecognitionKind) -> bool: ...

    def recognize(self, request: RecognitionRequestV1) -> RecognitionResultV1: ...


def _load_image(path: str):
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - deployment boundary
        raise RuntimeError("RecognitionService requires opencv-python") from error
    image = cv2.imread(str(Path(path)), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode recognition image: {path}")
    return image


def _crop(image: Any, region: SourcePixelRect | None) -> tuple[Any, int, int]:
    if region is None:
        return image, 0, 0
    height, width = image.shape[:2]
    if region.x + region.width > width or region.y + region.height > height:
        raise ValueError("recognition region is outside the source image")
    return (
        image[region.y : region.y + region.height, region.x : region.x + region.width],
        region.x,
        region.y,
    )


def _rect_from_points(points: Any, *, offset_x: int = 0, offset_y: int = 0):
    try:
        xs = [int(round(float(item[0]))) for item in points]
        ys = [int(round(float(item[1]))) for item in points]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return SourcePixelRect(
        x=min(xs) + offset_x,
        y=min(ys) + offset_y,
        width=max(1, max(xs) - min(xs)),
        height=max(1, max(ys) - min(ys)),
    )


class RapidOCRBackend:
    name = "rapidocr_onnxruntime"

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine
        try:
            self.version = importlib.metadata.version("rapidocr-onnxruntime")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - deployment boundary
            self.version = "unavailable"

    def supports(self, kind: RecognitionKind) -> bool:
        return kind == "ocr"

    def recognize(self, request: RecognitionRequestV1) -> RecognitionResultV1:
        if not self.supports(request.kind):
            raise ValueError(f"{self.name} does not support {request.kind}")
        image, offset_x, offset_y = _crop(_load_image(request.image_path), request.region)
        engine = self._engine
        if engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
            except ImportError as error:  # pragma: no cover - deployment boundary
                raise RuntimeError("RapidOCR backend is not installed") from error
            engine = RapidOCR()
            self._engine = engine
        raw, _elapsed = engine(image)
        detections: list[RecognitionDetectionV1] = []
        for item in raw or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            text = str(item[1]).strip()
            try:
                score = float(item[2])
            except (TypeError, ValueError):
                continue
            if text:
                detections.append(
                    RecognitionDetectionV1(
                        text=text,
                        score=max(0.0, min(1.0, score)),
                        bounds=_rect_from_points(item[0], offset_x=offset_x, offset_y=offset_y),
                    )
                )
        return RecognitionResultV1(
            backend=self.name,
            backend_version=self.version,
            kind=request.kind,
            hit=bool(detections),
            detections=tuple(detections),
        )


class OpenCVRecognitionBackend:
    """Deterministic local color/template backend for typed recognition requests."""

    name = "opencv"

    def __init__(self) -> None:
        try:
            self.version = importlib.metadata.version("opencv-python")
        except importlib.metadata.PackageNotFoundError:
            try:
                self.version = importlib.metadata.version("opencv-python-headless")
            except importlib.metadata.PackageNotFoundError:  # pragma: no cover
                self.version = "unavailable"
        self._image_cache: dict[tuple[str, int, int], Any] = {}
        self._image_cache_order: list[tuple[str, int, int]] = []

    def supports(self, kind: RecognitionKind) -> bool:
        return kind in {"color_match", "template_match"}

    @staticmethod
    def _cv2():
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - deployment boundary
            raise RuntimeError("OpenCV recognition backend is not installed") from error
        return cv2

    def _load_image(self, path: str) -> Any:
        """Decode one immutable artifact once across its typed region requests."""

        resolved = Path(path).resolve()
        stat = resolved.stat()
        key = (str(resolved), stat.st_mtime_ns, stat.st_size)
        cached = self._image_cache.get(key)
        if cached is not None:
            return cached
        image = _load_image(str(resolved))
        stale = [item for item in self._image_cache_order if item[0] == key[0]]
        for item in stale:
            self._image_cache.pop(item, None)
            self._image_cache_order.remove(item)
        self._image_cache[key] = image
        self._image_cache_order.append(key)
        while len(self._image_cache_order) > 4:
            evicted = self._image_cache_order.pop(0)
            self._image_cache.pop(evicted, None)
        return image

    def _color_match(self, request: RecognitionRequestV1) -> RecognitionResultV1:
        cv2 = self._cv2()
        image, offset_x, offset_y = _crop(self._load_image(request.image_path), request.region)
        working = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if request.color_space == "hsv" else image
        mask = None
        for lower, upper in zip(request.color_lower, request.color_upper, strict=True):
            current = cv2.inRange(working, lower, upper)
            mask = current if mask is None else cv2.bitwise_or(mask, current)
        if mask is None:  # model validation should make this unreachable
            raise ValueError("color_match has no color ranges")
        area = max(1, int(mask.shape[0] * mask.shape[1]))
        coverage = float(cv2.countNonZero(mask)) / area
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(grayscale, 80, 160)
        edge_density = float(cv2.countNonZero(edges)) / area
        bounds = SourcePixelRect(
            x=offset_x,
            y=offset_y,
            width=max(1, int(image.shape[1])),
            height=max(1, int(image.shape[0])),
        )
        detection = RecognitionDetectionV1(
            score=max(0.0, min(1.0, coverage)),
            bounds=bounds,
            raw={
                "coverage_ratio": coverage,
                "edge_density": edge_density,
                "color_space": request.color_space,
            },
        )
        return RecognitionResultV1(
            backend=self.name,
            backend_version=self.version,
            kind=request.kind,
            hit=coverage >= request.threshold,
            detections=(detection,),
        )

    def _template_match(self, request: RecognitionRequestV1) -> RecognitionResultV1:
        cv2 = self._cv2()
        image, offset_x, offset_y = _crop(self._load_image(request.image_path), request.region)
        detections: list[RecognitionDetectionV1] = []
        for template_path in request.template_paths:
            template, _, _ = _crop(self._load_image(template_path), request.template_region)
            if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
                raise ValueError("template is larger than the recognition region")
            response = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _minimum, maximum, _minimum_location, maximum_location = cv2.minMaxLoc(response)
            score = max(0.0, min(1.0, float(maximum)))
            detections.append(
                RecognitionDetectionV1(
                    score=score,
                    bounds=SourcePixelRect(
                        x=offset_x + int(maximum_location[0]),
                        y=offset_y + int(maximum_location[1]),
                        width=max(1, int(template.shape[1])),
                        height=max(1, int(template.shape[0])),
                    ),
                    raw={"template_path": template_path},
                )
            )
        best = max((item.score for item in detections), default=0.0)
        return RecognitionResultV1(
            backend=self.name,
            backend_version=self.version,
            kind=request.kind,
            hit=best >= request.threshold,
            detections=tuple(detections),
            raw={"best_score": best},
        )

    def recognize(self, request: RecognitionRequestV1) -> RecognitionResultV1:
        if not self.supports(request.kind):
            raise ValueError(f"{self.name} does not support {request.kind}")
        if request.kind == "color_match":
            return self._color_match(request)
        return self._template_match(request)


class MaaRecognitionBackend:
    """Maa Resource + Tasker recognition backend bound to a DeviceGateway controller."""

    name = "maafw"

    def __init__(self, *, resource: Any, tasker: Any, version: str) -> None:
        self.resource = resource
        self.tasker = tasker
        self.version = version

    def supports(self, kind: RecognitionKind) -> bool:
        return kind in {
            "ocr",
            "template_match",
            "feature_match",
            "color_match",
            "pipeline",
        }

    @staticmethod
    def _roi(request: RecognitionRequestV1):
        if request.region is None:
            return (0, 0, 0, 0)
        return (
            request.region.x,
            request.region.y,
            request.region.width,
            request.region.height,
        )

    def _direct_parameter(self, request: RecognitionRequestV1):
        from maa.pipeline import (
            JColorMatch,
            JFeatureMatch,
            JOCR,
            JRecognitionType,
            JTemplateMatch,
        )

        roi = self._roi(request)
        if request.kind == "ocr":
            return (
                JRecognitionType.OCR,
                JOCR(expected=list(request.expected_text), roi=roi, threshold=request.threshold),
            )
        if request.kind == "template_match":
            return (
                JRecognitionType.TemplateMatch,
                JTemplateMatch(
                    template=list(request.template_paths),
                    roi=roi,
                    threshold=[request.threshold],
                ),
            )
        if request.kind == "feature_match":
            return (
                JRecognitionType.FeatureMatch,
                JFeatureMatch(template=list(request.template_paths), roi=roi),
            )
        if request.kind == "color_match":
            return (
                JRecognitionType.ColorMatch,
                JColorMatch(
                    lower=[list(item) for item in request.color_lower],
                    upper=[list(item) for item in request.color_upper],
                    roi=roi,
                ),
            )
        raise ValueError("pipeline requests use the resource entry path")

    @staticmethod
    def _detection_from_result(result: Any) -> RecognitionDetectionV1:
        raw = dict(getattr(result, "__dict__", {}) or {})
        text = getattr(result, "text", None)
        score = getattr(result, "score", getattr(result, "confidence", 1.0))
        box = getattr(result, "box", None)
        bounds = None
        if box is not None:
            if isinstance(box, (list, tuple)) and len(box) >= 4:
                values = list(box[:4])
            else:
                values = [getattr(box, name, None) for name in ("x", "y", "w", "h")]
            if all(value is not None for value in values):
                bounds = SourcePixelRect(
                    x=int(values[0]),
                    y=int(values[1]),
                    width=max(1, int(values[2])),
                    height=max(1, int(values[3])),
                )
        return RecognitionDetectionV1(
            text=str(text) if text is not None else None,
            score=max(0.0, min(1.0, float(score))),
            bounds=bounds,
            raw=raw,
        )

    def recognize(self, request: RecognitionRequestV1) -> RecognitionResultV1:
        if request.kind == "pipeline":
            job = self.tasker.post_task(str(request.pipeline_entry)).wait()
            detail = job.get() if hasattr(job, "get") else None
            succeeded = bool(job.succeeded)
            raw = dict(getattr(detail, "raw_detail", {}) or {}) if detail else {}
            return RecognitionResultV1(
                backend=self.name,
                backend_version=self.version,
                kind=request.kind,
                hit=succeeded,
                raw=raw,
            )
        image = _load_image(request.image_path)
        recognition_type, recognition_parameter = self._direct_parameter(request)
        job = self.tasker.post_recognition(
            recognition_type,
            recognition_parameter,
            image,
        ).wait()
        task_detail = job.get() if hasattr(job, "get") else None
        detail = None
        node_ids = tuple(getattr(task_detail, "node_id_list", ()) or ())
        if node_ids and hasattr(self.tasker, "get_node_detail"):
            node_detail = self.tasker.get_node_detail(node_ids[-1])
            detail = getattr(node_detail, "recognition", None)
        results = tuple(
            self._detection_from_result(item)
            for item in (getattr(detail, "filtered_results", None) or ())
        )
        return RecognitionResultV1(
            backend=self.name,
            backend_version=self.version,
            kind=request.kind,
            hit=bool(getattr(detail, "hit", False)) and bool(job.succeeded),
            detections=results,
            raw=dict(getattr(detail, "raw_detail", {}) or {}) if detail else {},
        )


class RecognitionService:
    """Select exactly one registered backend for every recognition request."""

    def __init__(self, backends: tuple[RecognitionBackend, ...] | None = None) -> None:
        configured = backends or (RapidOCRBackend(), OpenCVRecognitionBackend())
        self.backends = {backend.name: backend for backend in configured}
        if len(self.backends) != len(configured):
            raise ValueError("RecognitionService backend names must be unique")

    def recognize(
        self,
        request: RecognitionRequestV1,
        *,
        backend: str | None = None,
    ) -> RecognitionResultV1:
        if backend is not None:
            selected = self.backends.get(backend)
            if selected is None:
                raise KeyError(f"unknown recognition backend: {backend}")
            if not selected.supports(request.kind):
                raise ValueError(f"recognition backend {backend} does not support {request.kind}")
            return selected.recognize(request)
        candidates = [item for item in self.backends.values() if item.supports(request.kind)]
        if len(candidates) != 1:
            raise RuntimeError(
                f"recognition request must resolve to one backend; got {len(candidates)}"
            )
        return candidates[0].recognize(request)

    def ocr_region(
        self,
        image_path: Path,
        region: SourcePixelRect | None = None,
        *,
        backend: str | None = None,
    ) -> RecognitionResultV1:
        return self.recognize(
            RecognitionRequestV1(
                kind="ocr",
                image_path=str(image_path),
                region=region,
            ),
            backend=backend,
        )
