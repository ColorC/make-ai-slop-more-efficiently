from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.omniparser_worker import (
    normalize_elements,
)
from omnicompany.packages.domains.game_observatory.visual_locator import (
    OmniParserRuntime,
)


def test_worker_normalizes_ratio_bbox_to_source_pixels():
    elements = normalize_elements(
        [
            {
                "type": "icon",
                "bbox": [0.9, 0.1, 0.98, 0.2],
                "interactivity": True,
                "content": "clover",
                "source": "box_yolo_content_yolo",
            }
        ],
        width=1080,
        height=1920,
    )
    assert elements[0]["source_bounds"] == {
        "x": 972,
        "y": 192,
        "width": 87,
        "height": 192,
    }
    assert elements[0]["interactivity"] is True


def test_runtime_probe_is_fail_closed_when_assets_are_missing(tmp_path: Path):
    runtime = OmniParserRuntime(
        tmp_path / "store",
        source_root=tmp_path / "missing-source",
    )
    result = runtime.probe()
    assert result["ready"] is False
    assert not any(result["source_files"].values())
    assert not any(result["weight_files"].values())


def test_runtime_setup_writes_manifest_without_download(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("README.md", "LICENSE"):
        (source / name).write_text(name, encoding="utf-8")
    (source / "util").mkdir()
    for name in ("omniparser.py", "utils.py"):
        (source / "util" / name).write_text(name, encoding="utf-8")
    runtime = OmniParserRuntime(tmp_path / "store", source_root=source)
    monkeypatch.setattr(
        runtime,
        "probe",
        lambda: {"ready": False, "locator": "omniparser-v2"},
    )
    result = runtime.setup(download=False)
    manifest = Path(result["manifest"])
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["locator"] == "omniparser-v2"