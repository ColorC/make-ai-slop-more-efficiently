from __future__ import annotations

import hashlib
import io
import struct
import xml.etree.ElementTree as ET
import zlib
from functools import lru_cache
from pathlib import Path

from .models import ArtifactRef, GameReport


VISUAL_ARTIFACT_KINDS = frozenset(
    {
        "screenshot",
        "video_frame",
        "annotated_plate",
        "layout_spec",
        "wireframe",
        "wireflow",
        "state_diagram",
        "interaction_diagram",
        "resource_diagram",
        "balance_table",
        "feedback_timeline",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_png(body: bytes) -> tuple[int, int, dict[str, float | int]]:
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG signature is invalid")
    offset = 8
    width = height = 0
    bit_depth = color_type = interlace = -1
    idat = bytearray()
    saw_end = False
    while offset + 12 <= len(body):
        size = struct.unpack(">I", body[offset : offset + 4])[0]
        kind = body[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(body):
            raise ValueError("PNG chunk is truncated")
        payload = body[offset + 8 : offset + 8 + size]
        expected_crc = struct.unpack(">I", body[offset + 8 + size : end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError(f"PNG {kind.decode('ascii', errors='replace')} CRC is invalid")
        if kind == b"IHDR":
            if len(payload) != 13:
                raise ValueError("PNG IHDR is invalid")
            width, height = struct.unpack(">II", payload[:8])
            bit_depth = payload[8]
            color_type = payload[9]
            interlace = payload[12]
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            saw_end = True
            break
        offset = end
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions are missing")
    if not idat:
        raise ValueError("PNG pixel data is missing")
    if not saw_end:
        raise ValueError("PNG end marker is missing")
    try:
        decoded = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"PNG pixel data cannot be decoded: {exc}") from exc
    if not decoded:
        raise ValueError("PNG decoded pixel data is empty")
    try:
        metrics = _pillow_visual_metrics(body)
    except ImportError:  # pragma: no cover - minimal installations use the stdlib fallback
        metrics = _png_visual_metrics(
            decoded,
            width=width,
            height=height,
            bit_depth=bit_depth,
            color_type=color_type,
            interlace=interlace,
        )
    return width, height, metrics


def _pillow_visual_metrics(body: bytes) -> dict[str, float | int]:
    """Decode a small analysis copy in native code; keeps API reads comfortably sub-second."""
    from PIL import Image

    with Image.open(io.BytesIO(body)) as image:
        image.thumbnail((256, 256))
        luminance = image.convert("L")
        low, high = luminance.getextrema()
        histogram = luminance.histogram()
        sample_count = sum(histogram)
        dark = sum(histogram[:6])
        colors = image.convert("RGB").getcolors(maxcolors=4096)
        sampled_colors = len(colors) if colors is not None else 4097
    return {
        "content_metrics_available": 1,
        "sample_count": sample_count,
        "sampled_colors": sampled_colors,
        "luma_min": low,
        "luma_max": high,
        "luma_range": high - low,
        "dark_ratio": dark / max(sample_count, 1),
    }


def _png_visual_metrics(
    decoded: bytes,
    *,
    width: int,
    height: int,
    bit_depth: int,
    color_type: int,
    interlace: int,
) -> dict[str, float | int]:
    """Return cheap pixel-content metrics for common 8-bit, non-interlaced PNGs.

    Structural validity is not enough for evidence: Android can produce a perfectly
    valid all-black PNG while an app surface is still starting.  We intentionally
    implement the PNG filters here instead of adding a heavyweight image dependency
    to the storage boundary.  Unsupported encodings remain structurally valid but do
    not receive content metrics.
    """
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if bit_depth != 8 or interlace != 0 or channels is None:
        return {"content_metrics_available": 0}
    row_bytes = width * channels
    expected = height * (row_bytes + 1)
    if len(decoded) != expected:
        raise ValueError(
            f"PNG decoded pixel length is invalid: expected {expected}, got {len(decoded)}"
        )

    previous = bytearray(row_bytes)
    luma_min = 255
    luma_max = 0
    dark = 0
    samples = 0
    colors: set[tuple[int, int, int]] = set()
    sample_stride = max(1, (width * height) // 20000)
    cursor = 0
    pixel_index = 0

    def paeth(a: int, b: int, c: int) -> int:
        prediction = a + b - c
        pa = abs(prediction - a)
        pb = abs(prediction - b)
        pc = abs(prediction - c)
        if pa <= pb and pa <= pc:
            return a
        return b if pb <= pc else c

    for _ in range(height):
        filter_type = decoded[cursor]
        cursor += 1
        raw = decoded[cursor : cursor + row_bytes]
        cursor += row_bytes
        row = bytearray(row_bytes)
        for index, value in enumerate(raw):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                reconstructed = value
            elif filter_type == 1:
                reconstructed = value + left
            elif filter_type == 2:
                reconstructed = value + above
            elif filter_type == 3:
                reconstructed = value + ((left + above) // 2)
            elif filter_type == 4:
                reconstructed = value + paeth(left, above, upper_left)
            else:
                raise ValueError(f"PNG filter type {filter_type} is unsupported")
            row[index] = reconstructed & 0xFF

        for offset in range(0, row_bytes, channels):
            if pixel_index % sample_stride == 0:
                if color_type in {0, 4}:
                    red = green = blue = row[offset]
                else:
                    red, green, blue = row[offset : offset + 3]
                luma = (54 * red + 183 * green + 19 * blue) // 256
                luma_min = min(luma_min, luma)
                luma_max = max(luma_max, luma)
                dark += int(luma <= 5)
                samples += 1
                if len(colors) < 4096:
                    colors.add((red, green, blue))
            pixel_index += 1
        previous = row

    return {
        "content_metrics_available": 1,
        "sample_count": samples,
        "sampled_colors": len(colors),
        "luma_min": luma_min,
        "luma_max": luma_max,
        "luma_range": luma_max - luma_min,
        "dark_ratio": dark / max(samples, 1),
    }


def _validate_jpeg(body: bytes) -> tuple[int, int]:
    if not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
        raise ValueError("JPEG boundary markers are invalid")
    offset = 2
    while offset + 4 <= len(body):
        if body[offset] != 0xFF:
            offset += 1
            continue
        marker = body[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(body):
            break
        size = struct.unpack(">H", body[offset : offset + 2])[0]
        if size < 2 or offset + size > len(body):
            raise ValueError("JPEG segment is truncated")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if size < 7:
                raise ValueError("JPEG frame header is invalid")
            height, width = struct.unpack(">HH", body[offset + 3 : offset + 7])
            if width <= 0 or height <= 0:
                raise ValueError("JPEG dimensions are invalid")
            return width, height
        offset += size
    raise ValueError("JPEG frame dimensions are missing")


def _validate_svg(body: bytes) -> None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"SVG cannot be parsed: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG root element is not svg")


@lru_cache(maxsize=1024)
def _artifact_file_issues_cached(
    artifact_id: str,
    kind: str,
    path_value: str,
    expected_sha: str,
    media_type_value: str,
    size: int,
    mtime_ns: int,
) -> tuple[str, ...]:
    del size, mtime_ns  # cache-key invalidators
    path = Path(path_value)
    if not path.is_file():
        return (f"{artifact_id}: artifact file is missing",)
    actual = _sha256(path)
    if actual != expected_sha:
        return (f"{artifact_id}: artifact SHA-256 mismatch",)
    if kind == "video":
        body = path.read_bytes()
        if len(body) < 16 or body[4:8] != b"ftyp":
            return (f"{artifact_id}: MP4 container is invalid",)
        import cv2

        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                return (f"{artifact_id}: MP4 video stream cannot be opened",)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if width <= 0 or height <= 0:
                return (f"{artifact_id}: MP4 video dimensions are invalid",)
            decoded = 0
            usable = 0
            for _ in range(5):
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                decoded += 1
                if int(frame.max()) > 5 and int(frame.max()) - int(frame.min()) >= 3:
                    usable += 1
            if decoded == 0:
                return (f"{artifact_id}: MP4 contains no decodable frames",)
            if usable == 0:
                return (f"{artifact_id}: MP4 begins with only black or uniform frames",)
        finally:
            capture.release()
        return ()
    if kind not in VISUAL_ARTIFACT_KINDS:
        return ()
    body = path.read_bytes()
    media_type = media_type_value.lower()
    suffix = path.suffix.lower()
    try:
        if media_type == "image/png" or suffix == ".png":
            _, _, metrics = _validate_png(body)
            if kind in {"screenshot", "video_frame"} and metrics.get(
                "content_metrics_available"
            ):
                luma_max = int(metrics["luma_max"])
                luma_range = int(metrics["luma_range"])
                sampled_colors = int(metrics["sampled_colors"])
                if luma_max <= 5:
                    raise ValueError("PNG screenshot is an all-black or near-black frame")
                if luma_range < 3 and sampled_colors < 4:
                    raise ValueError("PNG screenshot is visually uniform")
        elif media_type in {"image/jpeg", "image/jpg"} or suffix in {".jpg", ".jpeg"}:
            _validate_jpeg(body)
        elif media_type == "image/svg+xml" or suffix == ".svg":
            _validate_svg(body)
        elif media_type == "image/webp" or suffix == ".webp":
            if len(body) < 16 or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
                raise ValueError("WebP container is invalid")
        else:
            raise ValueError(
                f"unsupported public visual media type {media_type_value or suffix or 'unknown'}"
            )
    except ValueError as exc:
        return (f"{artifact_id}: {exc}",)
    return ()


def artifact_file_issues(artifact: ArtifactRef) -> list[str]:
    path = Path(artifact.path)
    if not path.is_file():
        return [f"{artifact.id}: artifact file is missing"]
    stat = path.stat()
    return list(
        _artifact_file_issues_cached(
            artifact.id,
            artifact.kind,
            str(path),
            artifact.sha256,
            artifact.media_type or "",
            stat.st_size,
            stat.st_mtime_ns,
        )
    )


def public_artifact_issues(report: GameReport) -> list[str]:
    return sorted(
        issue
        for artifact in report.artifacts
        if artifact.metadata.get("public") is True
        for issue in artifact_file_issues(artifact)
    )


def assert_public_artifacts(report: GameReport) -> None:
    issues = public_artifact_issues(report)
    if issues:
        raise ValueError("public artifact invalid: " + "; ".join(issues))
