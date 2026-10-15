"""Unpack the text-bearing Addressables assets of the shipped game package.

Two carriers live in the package and neither of them survives Lua decompilation:

``Assets/Text/GameText/game_text_<language>.json``
    One flat JSON object per language, holding every localization key the UI,
    the story recap and the map-explore dialogue look up at runtime.  It is
    packed into a single ``data_assets_assets/text_*.bundle``.

``Assets/DynamicConfig/Story/<prefix>/{Story,GameText}_<prefix>.lua.bytes``
    One script/text pair per story prefix, packed one bundle per prefix.  Only
    part of the catalogue's prefixes ship inside the package; the rest arrive
    from the CDN and land in the Unity asset cache on the device.

The module only reads.  It never decrypts: when a ``.lua.bytes`` payload is not
plain Lua the magic bytes are recorded and the payload is written out verbatim
so a later, separately-authorized step can pick it up.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import UnityPy


LOCALIZATION_SCHEMA = "game-observatory.localization-index.v1"
STORY_SCHEMA = "game-observatory.packaged-story-index.v1"

CATALOG_ENTRY = "assets/aa/catalog.json"
TEXT_BUNDLE = re.compile(r"^assets/aa/android/data_assets_assets/text_[0-9a-f]{32}\.bundle$")
STORY_BUNDLE = re.compile(
    r"^assets/aa/android/dynamicconfiglocal_assets_assets/dynamicconfig/story/"
    r"(?P<prefix>.+)_[0-9a-f]{32}\.bundle$"
)
CATALOG_STORY = re.compile(r"Assets/DynamicConfig/Story/([^/]+)/Story_")
CATALOG_GAME_TEXT = re.compile(r"Assets/Text/GameText/([A-Za-z0-9_]+)\.json")
GAME_TEXT_ASSET = re.compile(r"^game_text_(?P<language>[a-z]{2})$")
STORY_ASSET = re.compile(r"^(?P<kind>Story|GameText)_(?P<prefix>.+)\.lua$")

# Every packaged and cached ``.lua.bytes`` payload starts with this run of
# bytes.  It is recorded, not interpreted: decrypting the payload is out of
# scope for this importer.
ENCRYPTED_HEADER = bytes.fromhex(
    "3164230564412db52520335c4f7a3d60121a482d2b7e4e374a2138336a431b692b"
)

DEFAULT_WRITTEN_LANGUAGES = ("cn", "en")


class AddressablesError(RuntimeError):
    """Raised when the package does not hold the expected text carriers."""


@dataclass
class UnpackSummary:
    """Deterministic counters for one unpack run."""

    languages: int = 0
    written_languages: tuple[str, ...] = ()
    localization_keys: int = 0
    catalog_prefixes: int = 0
    packaged_prefixes: int = 0
    cached_prefixes: int = 0
    obtained_prefixes: int = 0
    plaintext_assets: int = 0
    encrypted_assets: int = 0
    parsed_sentences: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exit_code(self) -> int:
        return 3 if self.errors else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "languages": self.languages,
            "written_languages": list(self.written_languages),
            "localization_keys": self.localization_keys,
            "catalog_prefixes": self.catalog_prefixes,
            "packaged_prefixes": self.packaged_prefixes,
            "cached_prefixes": self.cached_prefixes,
            "obtained_prefixes": self.obtained_prefixes,
            "not_obtained_prefixes": self.catalog_prefixes - self.obtained_prefixes,
            "plaintext_assets": self.plaintext_assets,
            "encrypted_assets": self.encrypted_assets,
            "parsed_sentences": self.parsed_sentences,
            "errors": list(self.errors),
        }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def text_assets(data: bytes) -> dict[str, bytes]:
    """Return every ``TextAsset`` in one AssetBundle, keyed by asset name.

    UnityPy hands the payload back as a ``str`` decoded with surrogate escapes,
    so the round trip below restores the original bytes for binary payloads and
    is a no-op for UTF-8 text.
    """

    assets: dict[str, bytes] = {}
    env = UnityPy.load(io.BytesIO(data))
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        parsed = obj.read()
        script = parsed.m_Script
        raw = (
            script.encode("utf-8", "surrogateescape")
            if isinstance(script, str)
            else bytes(script)
        )
        assets[str(parsed.m_Name)] = raw
    return assets


def classify_payload(raw: bytes) -> dict[str, Any]:
    """Say whether a ``.lua.bytes`` payload is readable Lua, without decrypting."""

    header = ENCRYPTED_HEADER[: min(len(ENCRYPTED_HEADER), len(raw))]
    if raw.startswith(header) and header:
        return {
            "status": "encrypted",
            "magic": raw[:16].hex(),
            "header_bytes": len(header),
            "note": "payload is not plain Lua; recorded and left verbatim",
        }
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "status": "unknown_binary",
            "magic": raw[:16].hex(),
            "header_bytes": 0,
            "note": "payload is neither the known encrypted form nor valid UTF-8",
        }
    return {"status": "plaintext", "magic": raw[:16].hex(), "header_bytes": 0, "note": ""}


def _key_prefix_counts(keys: Iterable[str]) -> dict[str, int]:
    counter: Counter[str] = Counter(str(key).split("_", 1)[0] for key in keys)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def extract_localization(
    apk: Path,
    out_dir: Path,
    *,
    written_languages: Sequence[str] = DEFAULT_WRITTEN_LANGUAGES,
) -> dict[str, Any]:
    """Write the requested ``game_text_<language>.json`` files and their index."""

    archive = zipfile.ZipFile(apk)
    entries = [name for name in archive.namelist() if TEXT_BUNDLE.match(name)]
    if not entries:
        raise AddressablesError(f"no localization bundle in {apk}")
    entry = sorted(entries)[0]
    bundle = archive.read(entry)
    assets = text_assets(bundle)

    languages: list[dict[str, Any]] = []
    key_sets: dict[str, frozenset[str]] = {}
    primary_keys: list[str] = []
    for name in sorted(assets):
        match = GAME_TEXT_ASSET.match(name)
        if match is None:
            continue
        language = match.group("language")
        raw = assets[name]
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise AddressablesError(f"{name} is not a JSON object")
        key_sets[language] = frozenset(payload)
        record: dict[str, Any] = {
            "language": language,
            "asset": name,
            "bytes": len(raw),
            "sha256": _sha256(raw),
            "key_count": len(payload),
            "written_as": None,
        }
        if language in written_languages:
            target = out_dir / f"{name}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            record["written_as"] = target.name
        if language == "cn":
            primary_keys = list(payload)
        languages.append(record)

    if not languages:
        raise AddressablesError(f"no game_text asset inside {entry}")

    catalog_languages = sorted(
        set(CATALOG_GAME_TEXT.findall(archive.read(CATALOG_ENTRY).decode("utf-8", "replace")))
    )
    reference_language = "cn" if "cn" in key_sets else sorted(key_sets)[0]
    reference = key_sets[reference_language]
    for record in languages:
        keys = key_sets[record["language"]]
        record["missing_vs_reference"] = len(reference - keys)
        record["extra_vs_reference"] = len(keys - reference)
    index = {
        "schema": LOCALIZATION_SCHEMA,
        "source_apk": apk.name,
        "source_apk_sha256": _sha256(apk.read_bytes()),
        "bundle_entry": entry,
        "bundle_sha256": _sha256(bundle),
        "language_count": len(languages),
        "catalog_language_count": len(catalog_languages),
        "catalog_languages": catalog_languages,
        "written_languages": [
            record["language"] for record in languages if record["written_as"]
        ],
        "reference_language": reference_language,
        "key_count": len(reference),
        "keys_identical_across_languages": all(
            keys == reference for keys in key_sets.values()
        ),
        "complete_languages": sorted(
            language for language, keys in key_sets.items() if keys == reference
        ),
        "incomplete_languages": sorted(
            language for language, keys in key_sets.items() if keys != reference
        ),
        "prefix_count": len(_key_prefix_counts(primary_keys or reference)),
        "prefix_counts": _key_prefix_counts(primary_keys or reference),
        "languages": languages,
    }
    _write_json(out_dir / "_index.json", index)
    return index


def _cached_story_assets(cache_root: Path | None) -> dict[str, dict[str, Any]]:
    """Collect story assets the CDN already delivered into the Unity asset cache."""

    found: dict[str, dict[str, Any]] = {}
    if cache_root is None or not cache_root.is_dir():
        return found
    for path in sorted(cache_root.rglob("__data")):
        try:
            assets = text_assets(path.read_bytes())
        except Exception:  # noqa: BLE001 - a foreign cache entry is not an error here
            continue
        for name, raw in assets.items():
            match = STORY_ASSET.match(name)
            if match is None:
                continue
            found.setdefault(name, {"raw": raw, "source": path.as_posix()})
    return found


def extract_story(
    apk: Path,
    out_dir: Path,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Write every obtainable story script/text pair and index what is missing."""

    archive = zipfile.ZipFile(apk)
    catalog = archive.read(CATALOG_ENTRY).decode("utf-8", "replace")
    catalog_prefixes = sorted(set(CATALOG_STORY.findall(catalog)))

    payloads: dict[str, dict[str, dict[str, Any]]] = {}
    for name in sorted(archive.namelist()):
        match = STORY_BUNDLE.match(name)
        if match is None:
            continue
        for asset, raw in text_assets(archive.read(name)).items():
            asset_match = STORY_ASSET.match(asset)
            if asset_match is None:
                continue
            payloads.setdefault(asset_match.group("prefix"), {})[asset_match.group("kind")] = {
                "asset": asset,
                "raw": raw,
                "origin": "package",
                "source": name,
            }

    for asset, record in _cached_story_assets(cache_root).items():
        asset_match = STORY_ASSET.match(asset)
        if asset_match is None:
            continue
        prefix = asset_match.group("prefix")
        payloads.setdefault(prefix, {}).setdefault(
            asset_match.group("kind"),
            {
                "asset": asset,
                "raw": record["raw"],
                "origin": "cdn_cache",
                "source": record["source"],
            },
        )

    prefixes: list[dict[str, Any]] = []
    plaintext = encrypted = sentences = 0
    for prefix in sorted(payloads):
        parts = payloads[prefix]
        origins = sorted({part["origin"] for part in parts.values()})
        entry: dict[str, Any] = {
            "prefix": prefix,
            "origin": origins[0] if len(origins) == 1 else "mixed",
            "assets": {},
            "parsed_sentences": 0,
        }
        target_dir = out_dir / prefix
        for kind in ("Story", "GameText"):
            part = parts.get(kind)
            if part is None:
                continue
            raw = part["raw"]
            verdict = classify_payload(raw)
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / f"{part['asset']}.bytes").write_bytes(raw)
            entry["assets"][kind] = {
                "asset": part["asset"],
                "origin": part["origin"],
                "source": part["source"],
                "bytes": len(raw),
                "sha256": _sha256(raw),
                "written_as": f"{part['asset']}.bytes",
                **verdict,
            }
            if verdict["status"] == "plaintext":
                plaintext += 1
                parsed = _parse_plaintext_story(raw)
                name = "story" if kind == "Story" else "game_text"
                _write_json(target_dir / f"{name}.json", parsed)
                entry["assets"][kind]["parsed_as"] = f"{name}.json"
                if kind == "Story":
                    entry["parsed_sentences"] = parsed.get("sentence_count", 0)
            else:
                encrypted += 1
        sentences += entry["parsed_sentences"]
        prefixes.append(entry)

    obtained = {entry["prefix"] for entry in prefixes}
    index = {
        "schema": STORY_SCHEMA,
        "source_apk": apk.name,
        "catalog_entry": CATALOG_ENTRY,
        "catalog_prefix_count": len(catalog_prefixes),
        "packaged_prefix_count": sum(
            1 for entry in prefixes if entry["origin"] == "package"
        ),
        "cdn_cached_prefix_count": sum(
            1 for entry in prefixes if entry["origin"] == "cdn_cache"
        ),
        "obtained_prefix_count": len(obtained),
        "not_obtained_prefix_count": len(catalog_prefixes) - len(obtained),
        "not_obtained_prefixes": [name for name in catalog_prefixes if name not in obtained],
        "encrypted_header_hex": ENCRYPTED_HEADER.hex(),
        "encrypted_header_bytes": len(ENCRYPTED_HEADER),
        "plaintext_asset_count": plaintext,
        "encrypted_asset_count": encrypted,
        "parsed_sentence_count": sentences,
        "prefixes": prefixes,
    }
    _write_json(out_dir / "_manifest.json", index)
    return index


def _parse_plaintext_story(raw: bytes) -> dict[str, Any]:
    """Parse a plain-Lua story payload through the shared literal parser."""

    from .lua_literal import LuaLiteralError, parse_lua_return

    text = raw.decode("utf-8-sig")
    try:
        rows = parse_lua_return(text)
    except LuaLiteralError as exc:
        return {"status": "parse_error", "reason": str(exc), "sentence_count": 0, "rows": []}
    if isinstance(rows, dict):
        count = len(rows)
    elif isinstance(rows, list):
        count = len(rows)
    else:
        count = 0
    return {"status": "parsed", "reason": None, "sentence_count": count, "rows": rows}


def unpack(
    apk: Path | str,
    out_dir: Path | str,
    *,
    cache_root: Path | str | None = None,
    written_languages: Sequence[str] = DEFAULT_WRITTEN_LANGUAGES,
) -> UnpackSummary:
    """Write ``localization/`` and ``story/`` products under *out_dir*."""

    apk_path = Path(apk)
    root = Path(out_dir)
    if not apk_path.is_file():
        raise FileNotFoundError(f"package does not exist: {apk_path}")
    cache = Path(cache_root) if cache_root is not None else None

    localization = extract_localization(
        apk_path, root / "localization", written_languages=written_languages
    )
    story = extract_story(apk_path, root / "story", cache_root=cache)
    return UnpackSummary(
        languages=localization["language_count"],
        written_languages=tuple(localization["written_languages"]),
        localization_keys=localization["key_count"],
        catalog_prefixes=story["catalog_prefix_count"],
        packaged_prefixes=story["packaged_prefix_count"],
        cached_prefixes=story["cdn_cached_prefix_count"],
        obtained_prefixes=story["obtained_prefix_count"],
        plaintext_assets=story["plaintext_asset_count"],
        encrypted_assets=story["encrypted_asset_count"],
        parsed_sentences=story["parsed_sentence_count"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path, help="package to read (read-only)")
    parser.add_argument("--out", required=True, type=Path, help="config directory to write into")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="pulled Unity asset cache root holding CDN-delivered story bundles",
    )
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        help="language short name to write out; repeatable (default: cn, en)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = unpack(
        args.apk,
        args.out,
        cache_root=args.cache_dir,
        written_languages=tuple(args.languages or DEFAULT_WRITTEN_LANGUAGES),
    )
    if args.as_json:
        print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        payload = summary.as_dict()
        print(
            f"localization: {payload['languages']} languages, "
            f"{payload['localization_keys']} keys, wrote "
            f"{', '.join(payload['written_languages'])}"
        )
        print(
            f"story: {payload['obtained_prefixes']}/{payload['catalog_prefixes']} prefixes "
            f"obtained ({payload['packaged_prefixes']} packaged, "
            f"{payload['cached_prefixes']} from CDN cache), "
            f"{payload['plaintext_assets']} plaintext / "
            f"{payload['encrypted_assets']} encrypted assets"
        )
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
