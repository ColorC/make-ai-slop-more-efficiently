from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FLORENCE_PROCESSOR_REVISION = "ceaf371f01ef66192264811b390bccad475a4f02"
FLORENCE_CAPTION_CODE_REVISION = "9803f52844ec1ae5df004e6089262e9a23e527fd"
MICRO_GLYPH_ALLOWLIST = "iI1l?"
MICRO_GLYPH_MIN_CONFIDENCE = 0.9
MICRO_GLYPH_TILE_HEIGHT = 640
MICRO_GLYPH_TILE_OVERLAP = 96


def local_huggingface_snapshot(model_id: str) -> Path | None:
    """Resolve the complete local main snapshot used by the isolated runtime."""

    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        return None
    model_root = Path(hf_home) / "hub" / f"models--{model_id.replace('/', '--')}"
    main_ref = model_root / "refs" / "main"
    if not main_ref.is_file():
        return None
    revision = main_ref.read_text(encoding="utf-8").strip()
    snapshot = model_root / "snapshots" / revision
    required = (
        "config.json",
        "preprocessor_config.json",
        "processing_florence2.py",
        "tokenizer.json",
    )
    return snapshot if revision and all((snapshot / name).is_file() for name in required) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def repair_metadata_cache_invalidator(metadata_finder: type[Any] | None = None) -> bool:
    """Repair a broken Python 3.12 MetadataPathFinder cache hook when present."""
    if metadata_finder is None:
        from importlib.metadata import MetadataPathFinder

        metadata_finder = MetadataPathFinder
    raw = inspect.getattr_static(metadata_finder, "invalidate_caches", None)
    if raw is None or isinstance(raw, classmethod):
        return False
    if not callable(raw):
        raise TypeError("MetadataPathFinder.invalidate_caches is not callable")
    setattr(metadata_finder, "invalidate_caches", classmethod(raw))
    return True


def allow_guarded_flash_attention_imports(dynamic_module_utils: Any = None) -> bool:
    if dynamic_module_utils is None:
        from transformers import dynamic_module_utils as resolved_utils

        dynamic_module_utils = resolved_utils
    original = dynamic_module_utils.get_imports
    if getattr(original, "_game_observatory_guarded_flash_patch", False):
        return False

    def get_imports(filename: str | Path) -> list[str]:
        imports = list(original(filename))
        if Path(filename).name == "modeling_florence2.py":
            imports = [item for item in imports if item != "flash_attn"]
        return imports

    get_imports._game_observatory_guarded_flash_patch = True  # type: ignore[attr-defined]
    dynamic_module_utils.get_imports = get_imports
    return True


def pinned_caption_model_processor(
    model_name: str,
    model_name_or_path: str,
    device: str | None = None,
) -> dict[str, Any]:
    if model_name != "florence2":
        raise ValueError(f"unsupported OmniParser caption model: {model_name}")
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if resolved_device != "cpu" else torch.float32
    processor_source = local_huggingface_snapshot("microsoft/Florence-2-base")
    if processor_source is None:
        raise FileNotFoundError("local Florence-2 processor snapshot is incomplete")
    processor = AutoProcessor.from_pretrained(
        str(processor_source),
        trust_remote_code=True,
        local_files_only=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        torch_dtype=dtype,
        trust_remote_code=True,
        code_revision=FLORENCE_CAPTION_CODE_REVISION,
        attn_implementation="eager",
        local_files_only=True,
    ).to(resolved_device)
    return {"model": model, "processor": processor}


def normalize_elements(
    parsed: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        raw_bbox = item.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(value) for value in raw_bbox]
        except (TypeError, ValueError):
            continue
        source_x1 = max(0, min(width - 1, math.floor(x1 * width)))
        source_y1 = max(0, min(height - 1, math.floor(y1 * height)))
        source_x2 = max(source_x1 + 1, min(width, math.ceil(x2 * width)))
        source_y2 = max(source_y1 + 1, min(height, math.ceil(y2 * height)))
        elements.append(
            {
                "id": f"omniparser.element.{index:04d}",
                "type": str(item.get("type") or "unknown"),
                "bbox_normalized_xyxy": [x1, y1, x2, y2],
                "source_bounds": {
                    "x": source_x1,
                    "y": source_y1,
                    "width": source_x2 - source_x1,
                    "height": source_y2 - source_y1,
                },
                "interactivity": bool(item.get("interactivity")),
                "interaction_candidate": bool(item.get("interactivity")),
                "interactivity_source": "omniparser",
                "content": str(item.get("content") or ""),
                "source": str(item.get("source") or ""),
            }
        )
    return elements


def _source_bounds_from_ocr_polygon(
    polygon: Any,
    *,
    width: int,
    height: int,
) -> dict[str, int] | None:
    if not isinstance(polygon, (list, tuple)) or len(polygon) < 4:
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (IndexError, TypeError, ValueError):
        return None
    left = max(0, min(width - 1, math.floor(min(xs))))
    top = max(0, min(height - 1, math.floor(min(ys))))
    right = max(left + 1, min(width, math.ceil(max(xs))))
    bottom = max(top + 1, min(height, math.ceil(max(ys))))
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def _bounds_center(bounds: dict[str, int]) -> tuple[float, float]:
    return (
        bounds["x"] + bounds["width"] / 2,
        bounds["y"] + bounds["height"] / 2,
    )


def _bounds_contains(bounds: dict[str, int], point: tuple[float, float]) -> bool:
    return (
        bounds["x"] <= point[0] <= bounds["x"] + bounds["width"]
        and bounds["y"] <= point[1] <= bounds["y"] + bounds["height"]
    )


def extract_micro_glyph_candidates(
    ocr_results: list[Any],
    *,
    width: int,
    height: int,
    existing_elements: list[dict[str, Any]],
    minimum_confidence: float = MICRO_GLYPH_MIN_CONFIDENCE,
) -> list[dict[str, Any]]:
    """Turn isolated, high-confidence i-like glyphs into reviewable control candidates."""
    occupied = [
        item["source_bounds"]
        for item in existing_elements
        if item.get("interaction_candidate") is True
        and isinstance(item.get("source_bounds"), dict)
    ]
    candidates: list[dict[str, Any]] = []
    ranked: list[tuple[float, str, dict[str, int]]] = []
    for result in ocr_results:
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            continue
        raw_text = str(result[1]).strip()
        try:
            confidence = float(result[2])
        except (TypeError, ValueError):
            continue
        if raw_text not in set(MICRO_GLYPH_ALLOWLIST) or confidence < minimum_confidence:
            continue
        glyph_bounds = _source_bounds_from_ocr_polygon(
            result[0],
            width=width,
            height=height,
        )
        if glyph_bounds is None:
            continue
        glyph_width = glyph_bounds["width"]
        glyph_height = glyph_bounds["height"]
        if not (8 <= glyph_width <= 50 and 12 <= glyph_height <= 65):
            continue
        ranked.append((confidence, raw_text, glyph_bounds))

    for confidence, raw_text, glyph_bounds in sorted(ranked, reverse=True):
        glyph_center = _bounds_center(glyph_bounds)
        if any(_bounds_contains(bounds, glyph_center) for bounds in occupied):
            continue
        horizontal_margin = max(12, round(glyph_bounds["height"] * 0.45))
        vertical_margin = max(8, round(glyph_bounds["height"] * 0.25))
        left = max(0, glyph_bounds["x"] - horizontal_margin)
        top = max(0, glyph_bounds["y"] - vertical_margin)
        right = min(
            width,
            glyph_bounds["x"] + glyph_bounds["width"] + horizontal_margin,
        )
        bottom = min(
            height,
            glyph_bounds["y"] + glyph_bounds["height"] + vertical_margin,
        )
        control_bounds = {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        }
        candidates.append(
            {
                "id": f"microglyph.element.{len(candidates):04d}",
                "type": "micro-glyph-control-candidate",
                "source_bounds": control_bounds,
                "glyph_bounds": glyph_bounds,
                "interactivity": False,
                "interaction_candidate": True,
                "interactivity_source": "easyocr-micro-glyph-heuristic",
                "content": raw_text,
                "normalized_glyph_family": "i-like",
                "candidate_confidence": round(confidence, 6),
                "source": "easyocr_micro_glyph",
            }
        )
        occupied.append(control_bounds)
    return candidates


def extract_aligned_text_navigation_candidates(
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Promote aligned text rows or bridged orphan text to nav candidates."""
    anchors = [
        item
        for item in elements
        if item.get("interaction_candidate") is True
        and isinstance(item.get("source_bounds"), dict)
        and item["source_bounds"]["height"] <= 180
    ]
    candidates: list[dict[str, Any]] = []
    text_elements = [
        item
        for item in elements
        if item.get("type") == "text"
        and item.get("interaction_candidate") is not True
        and isinstance(item.get("source_bounds"), dict)
        and str(item.get("content") or "").strip()
    ]
    for item in text_elements:
        bounds = item.get("source_bounds")
        assert isinstance(bounds, dict)
        if not (
            8 <= bounds["width"] <= 300
            and 18 <= bounds["height"] <= 90
        ):
            continue
        center = _bounds_center(bounds)
        row_peers = [
            peer
            for peer in text_elements
            if peer["source_bounds"]["width"] >= 80
            and peer["source_bounds"]["height"] >= 40
            and abs(_bounds_center(peer["source_bounds"])[1] - center[1]) <= 24
        ]
        row_centers = sorted(_bounds_center(peer["source_bounds"])[0] for peer in row_peers)
        aligned_text_row = (
            bounds["width"] >= 80
            and bounds["height"] >= 40
            and len(row_centers) >= 3
            and row_centers[-1] - row_centers[0] >= 400
        )
        aligned = [
            anchor
            for anchor in anchors
            if abs(_bounds_center(anchor["source_bounds"])[1] - center[1])
            <= max(24, bounds["height"] * 0.75)
        ]
        left = [
            anchor
            for anchor in aligned
            if _bounds_center(anchor["source_bounds"])[0] < center[0]
        ]
        right = [
            anchor
            for anchor in aligned
            if _bounds_center(anchor["source_bounds"])[0] > center[0]
        ]
        bridged_by_controls = bool(left and right)
        anchor_centers = sorted(
            _bounds_center(anchor["source_bounds"])[0] for anchor in aligned
        )
        nearest_anchor_distance = min(
            (abs(anchor_center - center[0]) for anchor_center in anchor_centers),
            default=math.inf,
        )
        nearest_anchor_spacing = min(
            (
                right_center - left_center
                for left_center, right_center in zip(
                    anchor_centers,
                    anchor_centers[1:],
                    strict=False,
                )
            ),
            default=math.inf,
        )
        center_inside_anchor = any(
            _bounds_contains(anchor["source_bounds"], center) for anchor in aligned
        )
        completed_control_sequence = (
            bounds["width"] >= 80
            and bounds["height"] >= 40
            and len(anchor_centers) >= 2
            and nearest_anchor_distance <= 420
            and nearest_anchor_spacing <= 450
            and not center_inside_anchor
        )
        if (
            not aligned_text_row
            and not bridged_by_controls
            and not completed_control_sequence
        ):
            continue
        candidates.append(
            {
                "id": f"alignedtext.element.{len(candidates):04d}",
                "type": "aligned-text-navigation-candidate",
                "source_bounds": dict(bounds),
                "interactivity": False,
                "interaction_candidate": True,
                "interactivity_source": "aligned-text-navigation-heuristic",
                "content": str(item.get("content") or ""),
                "source_element_id": str(item.get("id") or ""),
                "left_anchor_ids": [str(anchor.get("id") or "") for anchor in left],
                "right_anchor_ids": [str(anchor.get("id") or "") for anchor in right],
                "aligned_text_row_ids": [
                    str(peer.get("id") or "") for peer in row_peers
                ],
                "candidate_basis": (
                    "aligned-text-row"
                    if aligned_text_row
                    else (
                        "controls-on-both-sides"
                        if bridged_by_controls
                        else "aligned-control-sequence"
                    )
                ),
                "source": "aligned_text_navigation",
            }
        )
    return candidates


def extract_paired_edge_navigation_candidates(
    elements: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Mirror a missing mid-screen edge control when an upper edge stack proves the layout."""
    anchors = [
        item
        for item in elements
        if item.get("interaction_candidate") is True
        and isinstance(item.get("source_bounds"), dict)
        and item.get("type") == "icon"
    ]
    candidates: list[dict[str, Any]] = []
    for side in ("left", "right"):
        if side == "left":
            on_side = lambda bounds: bounds["x"] + bounds["width"] <= width * 0.15
        else:
            on_side = lambda bounds: bounds["x"] >= width * 0.85
        upper_stack = [
            item
            for item in anchors
            if on_side(item["source_bounds"])
            and _bounds_center(item["source_bounds"])[1] < height * 0.35
        ]
        if len(upper_stack) < 3:
            continue
        middle = [
            item
            for item in anchors
            if on_side(item["source_bounds"])
            and height * 0.45 <= _bounds_center(item["source_bounds"])[1] <= height * 0.7
            and item["source_bounds"]["width"] <= width * 0.16
            and item["source_bounds"]["height"] <= height * 0.12
        ]
        for anchor in middle:
            anchor_bounds = anchor["source_bounds"]
            mirrored = {
                "x": width - anchor_bounds["x"] - anchor_bounds["width"],
                "y": anchor_bounds["y"],
                "width": anchor_bounds["width"],
                "height": anchor_bounds["height"],
            }
            mirrored_center = _bounds_center(mirrored)
            if any(
                math.dist(_bounds_center(item["source_bounds"]), mirrored_center)
                <= max(48, mirrored["height"])
                for item in anchors
            ):
                continue
            candidates.append(
                {
                    "id": f"pairededge.element.{len(candidates):04d}",
                    "type": "paired-edge-navigation-candidate",
                    "source_bounds": mirrored,
                    "interactivity": False,
                    "interaction_candidate": True,
                    "interactivity_source": "paired-edge-navigation-heuristic",
                    "content": "",
                    "source_element_id": str(anchor.get("id") or ""),
                    "upper_stack_anchor_ids": [
                        str(item.get("id") or "") for item in upper_stack
                    ],
                    "mirrored_from_side": side,
                    "source": "paired_edge_navigation",
                }
            )
    return candidates


def read_micro_glyphs_tiled(
    reader: Any,
    image_array: Any,
    *,
    tile_height: int = MICRO_GLYPH_TILE_HEIGHT,
    overlap: int = MICRO_GLYPH_TILE_OVERLAP,
) -> tuple[list[Any], int]:
    """Run the expensive magnified OCR pass on bounded overlapping strips."""
    image_height = int(image_array.shape[0])
    if tile_height <= overlap or tile_height <= 0:
        raise ValueError("micro-glyph tile height must be greater than overlap")
    step = tile_height - overlap
    starts = list(range(0, image_height, step))
    if starts and starts[-1] + overlap >= image_height:
        starts.pop()
    results: list[Any] = []
    for top in starts:
        bottom = min(image_height, top + tile_height)
        tile = image_array[top:bottom]
        for polygon, text, confidence in reader.readtext(
            tile,
            detail=1,
            text_threshold=0.2,
            low_text=0.2,
            link_threshold=0.2,
            allowlist=MICRO_GLYPH_ALLOWLIST,
            canvas_size=2560,
            mag_ratio=2,
        ):
            translated = [
                [float(point[0]), float(point[1]) + top] for point in polygon
            ]
            results.append((translated, text, confidence))
    return results, len(starts)


class OmniParserEngine:
    """One loaded OmniParser/OCR engine that can serve many screenshot jobs."""

    def __init__(
        self,
        *,
        omniparser_home: Path,
        weights_root: Path,
        box_threshold: float = 0.05,
    ) -> None:
        self.home = omniparser_home.resolve()
        self.weights = weights_root.resolve()
        self.box_threshold = float(box_threshold)
        sys.path.insert(0, str(self.home))
        self.metadata_cache_repaired = repair_metadata_cache_invalidator()

        import numpy as np
        import torch
        from PIL import Image

        self.np = np
        self.torch = torch
        self.Image = Image
        self.guarded_flash_imports_patched = allow_guarded_flash_attention_imports()
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        startup_started = time.perf_counter()
        from util import omniparser as omniparser_module
        from util.utils import reader

        omniparser_module.get_caption_model_processor = pinned_caption_model_processor
        self.locator = omniparser_module.Omniparser(
            {
                "som_model_path": str(self.weights / "icon_detect" / "model.pt"),
                "caption_model_name": "florence2",
                "caption_model_path": str(self.weights / "icon_caption"),
                "BOX_TRESHOLD": self.box_threshold,
            }
        )
        self.reader = reader
        self.startup_seconds = time.perf_counter() - startup_started

    def locate(self, image_path: Path, output_dir: Path) -> dict[str, Any]:
        image_path = image_path.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        image_bytes = image_path.read_bytes()
        with self.Image.open(image_path) as image:
            width, height = image.size
        parse_started = time.perf_counter()
        annotated_base64, parsed = self.locator.parse(
            base64.b64encode(image_bytes).decode("ascii")
        )
        parse_seconds = time.perf_counter() - parse_started
        annotated_path = output_dir / "annotated.png"
        annotated_path.write_bytes(base64.b64decode(annotated_base64))
        raw_path = output_dir / "raw-elements.json"
        raw_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        elements = normalize_elements(parsed, width=width, height=height)
        aligned_text_candidates = extract_aligned_text_navigation_candidates(elements)
        elements.extend(aligned_text_candidates)
        paired_edge_candidates = extract_paired_edge_navigation_candidates(
            elements,
            width=width,
            height=height,
        )
        elements.extend(paired_edge_candidates)

        micro_glyph_started = time.perf_counter()
        with self.Image.open(image_path) as image:
            micro_glyph_ocr, micro_glyph_tile_count = read_micro_glyphs_tiled(
                self.reader,
                self.np.array(image.convert("RGB")),
            )
        micro_glyph_seconds = time.perf_counter() - micro_glyph_started
        raw_micro_glyph_path = output_dir / "raw-micro-glyph-ocr.json"
        raw_micro_glyph_path.write_text(
            json.dumps(
                micro_glyph_ocr,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )
        micro_glyph_candidates = extract_micro_glyph_candidates(
            micro_glyph_ocr,
            width=width,
            height=height,
            existing_elements=elements,
        )
        elements.extend(micro_glyph_candidates)
        torch = self.torch
        result = {
            "schema": "game-observatory.visual-locator-run.v1",
            "locator": "omniparser-v2",
            "generated_at": _utc_now(),
            "image": {
                "path": str(image_path),
                "sha256": _sha256(image_path),
                "width": width,
                "height": height,
            },
            "config": {
                "box_threshold": self.box_threshold,
                "coordinate_space": "source_pixels",
                "florence_processor_revision": FLORENCE_PROCESSOR_REVISION,
                "florence_caption_code_revision": FLORENCE_CAPTION_CODE_REVISION,
                "attention_implementation": "eager",
                "micro_glyph_supplement": {
                    "enabled": True,
                    "allowlist": MICRO_GLYPH_ALLOWLIST,
                    "minimum_confidence": MICRO_GLYPH_MIN_CONFIDENCE,
                    "tile_height": MICRO_GLYPH_TILE_HEIGHT,
                    "tile_overlap": MICRO_GLYPH_TILE_OVERLAP,
                    "candidate_status": "unconfirmed-interaction-candidate",
                },
                "aligned_text_navigation_supplement": {
                    "enabled": True,
                    "candidate_status": "unconfirmed-interaction-candidate",
                    "requires_aligned_controls_on_both_sides": True,
                },
                "paired_edge_navigation_supplement": {
                    "enabled": True,
                    "candidate_status": "unconfirmed-interaction-candidate",
                    "minimum_upper_edge_stack": 3,
                },
            },
            "metrics": {
                "startup_seconds": round(self.startup_seconds, 6),
                "parse_seconds": round(parse_seconds, 6),
                "element_count": len(elements),
                "interactable_count": sum(item["interactivity"] for item in elements),
                "interaction_candidate_count": sum(
                    item["interaction_candidate"] for item in elements
                ),
                "micro_glyph_seconds": round(micro_glyph_seconds, 6),
                "micro_glyph_tile_count": micro_glyph_tile_count,
                "micro_glyph_candidate_count": len(micro_glyph_candidates),
                "aligned_text_navigation_candidate_count": len(
                    aligned_text_candidates
                ),
                "paired_edge_navigation_candidate_count": len(
                    paired_edge_candidates
                ),
                "cuda_available": torch.cuda.is_available(),
                "cuda_device": (
                    torch.cuda.get_device_name(0)
                    if torch.cuda.is_available()
                    else None
                ),
                "peak_cuda_memory_bytes": (
                    int(torch.cuda.max_memory_allocated())
                    if torch.cuda.is_available()
                    else 0
                ),
                "metadata_cache_invalidator_repaired": (
                    self.metadata_cache_repaired
                ),
                "guarded_flash_attention_imports_patched": (
                    self.guarded_flash_imports_patched
                ),
            },
            "elements": elements,
            "annotated_image": str(annotated_path),
            "raw_elements": str(raw_path),
            "raw_micro_glyph_ocr": str(raw_micro_glyph_path),
        }
        result_path = output_dir / "result.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="game-observatory-omniparser-worker")
    parser.add_argument("--omniparser-home", type=Path, required=True)
    parser.add_argument("--weights-root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--box-threshold", type=float, default=0.05)
    args = parser.parse_args(argv)

    engine = OmniParserEngine(
        omniparser_home=args.omniparser_home,
        weights_root=args.weights_root,
        box_threshold=args.box_threshold,
    )
    result = engine.locate(args.image, args.output_dir)
    result_path = args.output_dir.resolve() / "result.json"
    print(
        json.dumps(
            {
                "ok": True,
                "result": str(result_path),
                "elements": len(elements),
                "parse_seconds": result["metrics"]["parse_seconds"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FLORENCE_CAPTION_CODE_REVISION",
    "FLORENCE_PROCESSOR_REVISION",
    "OmniParserEngine",
    "allow_guarded_flash_attention_imports",
    "extract_aligned_text_navigation_candidates",
    "extract_micro_glyph_candidates",
    "extract_paired_edge_navigation_candidates",
    "main",
    "normalize_elements",
    "pinned_caption_model_processor",
    "repair_metadata_cache_invalidator",
    "read_micro_glyphs_tiled",
]
