from __future__ import annotations

import hashlib
import struct
import xml.etree.ElementTree as ET
import zlib
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


def _validate_png(body: bytes) -> tuple[int, int]:
    if not body.startswith(b"\x89PNG\r\
\x1a\
"):
        raise ValueError("PNG signature is invalid")
    offset = 8
    width = height = 0
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
    return width, height


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


def artifact_file_issues(artifact: ArtifactRef) -> list[str]:
    path = Path(artifact.path)
    if not path.is_file():
        return [f"{artifact.id}: artifact file is missing"]
    actual = _sha256(path)
    if actual != artifact.sha256:
        return [f"{artifact.id}: artifact SHA-256 mismatch"]
    if artifact.kind not in VISUAL_ARTIFACT_KINDS:
        return []
    body = path.read_bytes()
    media_type = (artifact.media_type or "").lower()
    suffix = path.suffix.lower()
    try:
        if media_type == "image/png" or suffix == ".png":
            _validate_png(body)
        elif media_type in {"image/jpeg", "image/jpg"} or suffix in {".jpg", ".jpeg"}:
            _validate_jpeg(body)
        elif media_type == "image/svg+xml" or suffix == ".svg":
            _validate_svg(body)
        elif media_type == "image/webp" or suffix == ".webp":
            if len(body) < 16 or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
                raise ValueError("WebP container is invalid")
        else:
            raise ValueError(
                f"unsupported public visual media type {artifact.media_type or suffix or 'unknown'}"
            )
    except ValueError as exc:
        return [f"{artifact.id}: {exc}"]
    return []


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