"""Canonical, content-addressed OmniParser results for the unified AI-player CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from ..models import ArtifactRef, SourcePixelRect
from ..store import ObservatoryStore
from ..visual_locator import OmniParserRuntime, VisualLocatorError


_RESULT_ID = re.compile(r"^locator\.result\.([0-9a-f]{64})$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CanonicalVisualLocatorService:
    def __init__(
        self,
        store: ObservatoryStore,
        *,
        omniparser_home: Path | None = None,
    ) -> None:
        self.store = store
        self.omniparser_home = omniparser_home

    @property
    def root(self) -> Path:
        return self.store.root / "visual_locator"

    def _result_path(self, result_id: str) -> Path:
        match = _RESULT_ID.fullmatch(result_id)
        if match is None:
            raise VisualLocatorError("invalid canonical locator result ID")
        return self.root / "results" / f"{match.group(1)}.json"

    @staticmethod
    def _source_binding(
        *,
        source: ArtifactRef,
        environment_id: str,
        source_step_id: str,
        evidence_run_id: str,
        width: int,
        height: int,
        box_threshold: float,
    ) -> dict[str, Any]:
        return {
            "environment_id": environment_id,
            "source_step_id": source_step_id,
            "evidence_run_id": evidence_run_id,
            "source_artifact_id": source.id,
            "source_sha256": source.sha256,
            "width": width,
            "height": height,
            "config": {
                "locator": "omniparser-v2",
                "box_threshold": round(float(box_threshold), 6),
            },
        }

    def load_for_source(
        self,
        *,
        source: ArtifactRef,
        environment_id: str,
        source_step_id: str,
        evidence_run_id: str,
        width: int,
        height: int,
        box_threshold: float = 0.05,
    ) -> dict[str, Any] | None:
        """Load an exact current-source result without inference or directory scans."""

        binding = self._source_binding(
            source=source,
            environment_id=environment_id,
            source_step_id=source_step_id,
            evidence_run_id=evidence_run_id,
            width=width,
            height=height,
            box_threshold=box_threshold,
        )
        result_id = f"locator.result.{_canonical_hash(binding)}"
        if not self._result_path(result_id).is_file():
            return None
        result = self.load(result_id)
        if result.get("source") != binding:
            raise VisualLocatorError(
                "canonical locator result binding does not match its current source"
            )
        return result

    @staticmethod
    def _validate_source(source: ArtifactRef, *, width: int, height: int) -> Path:
        path = Path(source.path).resolve()
        if not path.is_file():
            raise VisualLocatorError(f"canonical source artifact is missing: {source.id}")
        if _sha256(path) != source.sha256:
            raise VisualLocatorError(f"canonical source artifact hash changed: {source.id}")
        with Image.open(path) as image:
            actual = image.size
        if actual != (width, height):
            raise VisualLocatorError(
                f"canonical source image is {actual[0]}x{actual[1]}, expected {width}x{height}"
            )
        return path

    @staticmethod
    def _normalize_elements(
        raw_elements: Any,
        *,
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_elements, list):
            raise VisualLocatorError("OmniParser result has no element list")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_elements:
            if not isinstance(raw, dict):
                raise VisualLocatorError("OmniParser element must be an object")
            element_id = str(raw.get("id") or raw.get("element_id") or "").strip()
            if not element_id or element_id in seen:
                raise VisualLocatorError("OmniParser element IDs must be non-empty and unique")
            bounds_payload = raw.get("source_bounds") or raw.get("bounds")
            try:
                bounds = SourcePixelRect.model_validate(bounds_payload)
            except ValueError as exc:
                raise VisualLocatorError(f"invalid bounds for {element_id}: {exc}") from exc
            if bounds.x + bounds.width > width or bounds.y + bounds.height > height:
                raise VisualLocatorError(f"element bounds exceed canonical source: {element_id}")
            seen.add(element_id)
            normalized.append(
                {
                    **raw,
                    "id": element_id,
                    "element_id": element_id,
                    "source_bounds": bounds.model_dump(mode="json"),
                    "bounds": bounds.model_dump(mode="json"),
                    "content": str(raw.get("content") or ""),
                    "type": str(raw.get("type") or "unknown"),
                    "interaction_candidate": bool(raw.get("interaction_candidate")),
                }
            )
        return normalized

    def _load_inference(
        self,
        path: Path,
        *,
        source_sha256: str,
        width: int,
        height: int,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            image = payload["image"]
            annotated = Path(str(payload["annotated_image"]))
            if (
                payload.get("locator") != "omniparser-v2"
                or image.get("sha256") != source_sha256
                or (int(image.get("width") or 0), int(image.get("height") or 0))
                != (width, height)
                or not annotated.is_file()
            ):
                return None
            self._normalize_elements(payload.get("elements"), width=width, height=height)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return payload

    def locate(
        self,
        *,
        source: ArtifactRef,
        environment_id: str,
        source_step_id: str,
        evidence_run_id: str,
        width: int,
        height: int,
        box_threshold: float = 0.05,
        timeout_seconds: float = 90,
    ) -> dict[str, Any]:
        source_path = self._validate_source(source, width=width, height=height)
        config = {
            "locator": "omniparser-v2",
            "box_threshold": round(float(box_threshold), 6),
        }
        config_hash = _canonical_hash(config)[:16]
        inference_dir = self.root / "cache" / source.sha256 / config_hash
        inference_path = inference_dir / "result.json"
        inference = self._load_inference(
            inference_path,
            source_sha256=source.sha256,
            width=width,
            height=height,
        )
        cache_hit = inference is not None
        if inference is None:
            runtime = OmniParserRuntime(
                self.store.root,
                source_root=self.omniparser_home,
            )
            inference = runtime.locate(
                source_path,
                inference_dir,
                box_threshold=box_threshold,
                timeout_seconds=timeout_seconds,
            )
        elements = self._normalize_elements(
            inference.get("elements"),
            width=width,
            height=height,
        )
        annotated_path = Path(str(inference.get("annotated_image") or "")).resolve()
        if not annotated_path.is_file():
            raise VisualLocatorError("OmniParser annotated image is missing")
        annotated_sha256 = _sha256(annotated_path)
        with Image.open(annotated_path) as annotated_image:
            if annotated_image.size != (width, height):
                raise VisualLocatorError("OmniParser annotated image dimensions changed")

        binding = self._source_binding(
            source=source,
            environment_id=environment_id,
            source_step_id=source_step_id,
            evidence_run_id=evidence_run_id,
            width=width,
            height=height,
            box_threshold=box_threshold,
        )
        binding_hash = _canonical_hash(binding)
        result_id = f"locator.result.{binding_hash}"
        result_path = self._result_path(result_id)
        artifact_suffix = binding_hash[:24]
        if result_path.is_file():
            existing = self.load(result_id)
            if existing.get("source") != binding:
                raise VisualLocatorError(
                    "canonical locator result binding does not match its identity"
                )
            result_artifact = self.store.get_artifact(
                f"art.locator-result.{artifact_suffix}"
            )
            assert result_artifact is not None  # checked by load()
            return {
                **existing,
                "result_artifact": result_artifact.model_dump(mode="json"),
                "cache_hit": cache_hit,
            }
        annotated_artifact = ArtifactRef(
            id=f"art.locator-annotation.{artifact_suffix}",
            kind="annotated_plate",
            path=str(annotated_path),
            sha256=annotated_sha256,
            captured_at=source.captured_at,
            run_id=source.run_id,
            media_type="image/png",
            metadata={
                **binding,
                "locator_result_id": result_id,
                "role": "visual_locator_annotation",
            },
        )
        self.store.save_artifact(annotated_artifact)

        preview_path = inference_dir / "annotated-preview.jpg"
        if not preview_path.is_file():
            with Image.open(annotated_path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((540, 960), Image.Resampling.LANCZOS)
                preview.save(preview_path, format="JPEG", quality=80, optimize=True)
        with Image.open(preview_path) as preview_image:
            preview_width, preview_height = preview_image.size
        preview_artifact = ArtifactRef(
            id=f"art.locator-preview.{artifact_suffix}",
            kind="annotated_plate",
            path=str(preview_path),
            sha256=_sha256(preview_path),
            captured_at=source.captured_at,
            run_id=source.run_id,
            media_type="image/jpeg",
            metadata={
                **binding,
                "locator_result_id": result_id,
                "annotated_artifact_id": annotated_artifact.id,
                "role": "visual_locator_preview",
                "preview_width": preview_width,
                "preview_height": preview_height,
            },
        )
        self.store.save_artifact(preview_artifact)

        payload = {
            "schema": "game-observatory.ai-player.visual-locator-result.v1",
            "id": result_id,
            "path": str(result_path),
            "locator": "omniparser-v2",
            "generated_at": str(inference.get("generated_at") or source.captured_at),
            "source": binding,
            "inference_cache": {
                "key": f"{source.sha256}:{config_hash}",
                "path": str(inference_path),
            },
            "annotated_artifact": annotated_artifact.model_dump(mode="json"),
            "agent_preview": preview_artifact.model_dump(mode="json"),
            "metrics": dict(inference.get("metrics") or {}),
            "elements": elements,
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_result = result_path.with_name(
            f".{result_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_result.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_result, result_path)
        finally:
            temporary_result.unlink(missing_ok=True)
        result_artifact = ArtifactRef(
            id=f"art.locator-result.{artifact_suffix}",
            kind="layout_spec",
            path=str(result_path),
            sha256=_sha256(result_path),
            captured_at=source.captured_at,
            run_id=source.run_id,
            media_type="application/json",
            metadata={
                **binding,
                "locator_result_id": result_id,
                "role": "visual_locator_result",
            },
        )
        self.store.save_artifact(result_artifact)
        return {
            **payload,
            "result_artifact": result_artifact.model_dump(mode="json"),
            "cache_hit": cache_hit,
        }

    def load(self, result_id: str) -> dict[str, Any]:
        path = self._result_path(result_id)
        if not path.is_file():
            raise VisualLocatorError(f"canonical locator result not found: {result_id}")
        match = _RESULT_ID.fullmatch(result_id)
        assert match is not None
        result_artifact = self.store.get_artifact(
            f"art.locator-result.{match.group(1)[:24]}"
        )
        if (
            result_artifact is None
            or Path(result_artifact.path).resolve() != path.resolve()
            or _sha256(path) != result_artifact.sha256
        ):
            raise VisualLocatorError("canonical locator result artifact hash changed")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VisualLocatorError(f"cannot read canonical locator result: {exc}") from exc
        if payload.get("id") != result_id or payload.get("path") != str(path):
            raise VisualLocatorError("canonical locator result identity is inconsistent")
        source = payload.get("source")
        if not isinstance(source, dict):
            raise VisualLocatorError("canonical locator result has no source binding")
        payload["elements"] = self._normalize_elements(
            payload.get("elements"),
            width=int(source.get("width") or 0),
            height=int(source.get("height") or 0),
        )
        return payload


__all__ = ["CanonicalVisualLocatorService"]
