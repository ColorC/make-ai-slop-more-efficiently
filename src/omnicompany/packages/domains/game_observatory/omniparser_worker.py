from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
                "content": str(item.get("content") or ""),
                "source": str(item.get("source") or ""),
            }
        )
    return elements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="game-observatory-omniparser-worker")
    parser.add_argument("--omniparser-home", type=Path, required=True)
    parser.add_argument("--weights-root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--box-threshold", type=float, default=0.05)
    args = parser.parse_args(argv)

    home = args.omniparser_home.resolve()
    weights = args.weights_root.resolve()
    image_path = args.image.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(home))

    from PIL import Image
    import torch

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    startup_started = time.perf_counter()
    from util.omniparser import Omniparser

    locator = Omniparser(
        {
            "som_model_path": str(weights / "icon_detect" / "model.pt"),
            "caption_model_name": "florence2",
            "caption_model_path": str(weights / "icon_caption"),
            "BOX_TRESHOLD": args.box_threshold,
        }
    )
    startup_seconds = time.perf_counter() - startup_started
    image_bytes = image_path.read_bytes()
    with Image.open(image_path) as image:
        width, height = image.size
    parse_started = time.perf_counter()
    annotated_base64, parsed = locator.parse(base64.b64encode(image_bytes).decode("ascii"))
    parse_seconds = time.perf_counter() - parse_started
    annotated_path = output_dir / "annotated.png"
    annotated_path.write_bytes(base64.b64decode(annotated_base64))
    raw_path = output_dir / "raw-elements.json"
    raw_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    elements = normalize_elements(parsed, width=width, height=height)
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
            "box_threshold": args.box_threshold,
            "coordinate_space": "source_pixels",
        },
        "metrics": {
            "startup_seconds": round(startup_seconds, 6),
            "parse_seconds": round(parse_seconds, 6),
            "element_count": len(elements),
            "interactable_count": sum(item["interactivity"] for item in elements),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device": (
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
            ),
            "peak_cuda_memory_bytes": (
                int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0
            ),
        },
        "elements": elements,
        "annotated_image": str(annotated_path),
        "raw_elements": str(raw_path),
    }
    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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


__all__ = ["main", "normalize_elements"]