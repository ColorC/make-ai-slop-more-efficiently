"""Pinned MaaFramework resource resolution for Game Observatory deployments."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .content_addressed_store import sha256_file


MAA_COMMON_ASSETS_COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
MAA_ZH_CN_V5_FILES = {
    "det.onnx": "8c3b7ee97913a7942b8565669dc9acbe8846fbbaf4b63e1d7fdb339005574a33",
    "keys.txt": "d1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b",
    "rec.onnx": "31fb844ce3a4aaf13e4bea62ae35f43bd9a509966061980c30db9b248c542a6b",
}


class MaaOCRResourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(default="game-observatory.maa-ocr-resource.v1", alias="schema")
    language: str = "zh-CN"
    model_family: str = "ppocr_v5"
    source_repository: str = "https://github.com/MaaXYZ/MaaCommonAssets"
    source_commit: str = MAA_COMMON_ASSETS_COMMIT
    directory: str
    files: dict[str, str]


def default_maa_ocr_model_path(repository_root: Path) -> Path:
    return (
        repository_root
        / "data/domains/game_observatory/resources/maa/ocr/ppocr_v5/zh_cn"
    )


def resolve_maa_ocr_model(repository_root: Path) -> MaaOCRResourceV1:
    directory = default_maa_ocr_model_path(repository_root).resolve()
    actual: dict[str, str] = {}
    for name, expected in MAA_ZH_CN_V5_FILES.items():
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"Maa OCR model file is missing: {path}")
        digest = sha256_file(path)
        if digest != expected:
            raise ValueError(f"Maa OCR model hash mismatch: {path}")
        actual[name] = digest
    return MaaOCRResourceV1(directory=str(directory), files=actual)
