# [OMNI] origin=codex created_by=codex intent="decrypt copied Romantic Restaurant saves without host SQLCipher"
"""Offline SQLCipher 4 page decryption for Romantic Restaurant save copies."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import struct
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from Crypto.Cipher import AES
from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SQLCipherParametersV1(_FrozenModel):
    """Explicit on-disk parameters recovered from the game's native library."""

    kdf_iter: int = Field(default=256000, ge=1)
    fast_kdf_iter: int = Field(default=3, ge=1)
    page_size: int = Field(default=4096, ge=512)
    hmac_algorithm: Literal["HMAC_SHA512"] = "HMAC_SHA512"
    kdf_algorithm: Literal["PBKDF2_HMAC_SHA512"] = "PBKDF2_HMAC_SHA512"
    plaintext_header_size: Literal[0] = 0

    @property
    def hmac_size(self) -> int:
        return hashlib.sha512().digest_size

    @property
    def reserve_size(self) -> int:
        raw = AES.block_size + self.hmac_size
        return ((raw + AES.block_size - 1) // AES.block_size) * AES.block_size


DEFAULT_PARAMETERS = SQLCipherParametersV1()


class OfflineDecryptResultV1(_FrozenModel):
    output_path: str
    encrypted_sha256: str
    page_count: int = Field(ge=1)
    wal_frames: int = Field(ge=0)
    wal_applied: bool


class SaveDecryptError(RuntimeError):
    """Encrypted save bytes did not satisfy the expected SQLCipher contract."""


class SaveKeyMissingError(SaveDecryptError):
    """The environment did not identify a readable JSON key file."""


def load_save_password(
    key_file: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bytes:
    """Read ``{"password": "..."}`` without exposing its value in errors."""

    environment = os.environ if environ is None else environ
    selected = key_file
    if selected is None:
        configured = environment.get("RR_SAVE_KEY_FILE")
        if not configured:
            raise SaveKeyMissingError("RR_SAVE_KEY_FILE is not set")
        selected = Path(configured)
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SaveKeyMissingError("save key file is missing or invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("password"), str):
        raise SaveKeyMissingError("save key JSON must contain a string password field")
    password = payload["password"].encode("utf-8")
    if not password:
        raise SaveKeyMissingError("save key password is empty")
    return password


# PBKDF2 with ``kdf_iter=256000`` costs about 157 ms per call on the driving
# host and is 98% of ``decrypt_ms``; the page AES pass is 0.0052 ms/KiB.  The
# database salt is the first 16 bytes of the container and SQLCipher only
# rewrites it on create/rekey, so the derived pair can be reused for every read
# of the same account.  The cache is keyed on a keyed digest of the password
# plus the salt and the KDF parameters, so a different key file, a rekeyed save
# or different parameters always re-derive instead of silently reusing.
_DERIVED_KEY_CACHE: dict[tuple[bytes, bytes, int, int], tuple[bytes, bytes]] = {}
_DERIVED_KEY_CACHE_LIMIT = 8
_DERIVED_KEY_CACHE_STATS = {"hits": 0, "misses": 0}


def clear_derived_key_cache() -> None:
    """Drop every cached derived key pair and reset the hit counters."""

    _DERIVED_KEY_CACHE.clear()
    _DERIVED_KEY_CACHE_STATS["hits"] = 0
    _DERIVED_KEY_CACHE_STATS["misses"] = 0


def derived_key_cache_stats() -> dict[str, int]:
    """Return ``{"hits", "misses", "entries"}`` for reporting and tests."""

    return {**_DERIVED_KEY_CACHE_STATS, "entries": len(_DERIVED_KEY_CACHE)}


def _derived_key_cache_key(
    password: bytes,
    salt: bytes,
    parameters: SQLCipherParametersV1,
) -> tuple[bytes, bytes, int, int]:
    fingerprint = hashlib.blake2b(
        password, digest_size=32, person=b"rr-save-kdf"
    ).digest()
    return (fingerprint, bytes(salt), parameters.kdf_iter, parameters.fast_kdf_iter)


def _derive_keys(
    password: bytes,
    salt: bytes,
    parameters: SQLCipherParametersV1,
) -> tuple[bytes, bytes]:
    cache_key = _derived_key_cache_key(password, salt, parameters)
    cached = _DERIVED_KEY_CACHE.get(cache_key)
    if cached is not None:
        _DERIVED_KEY_CACHE_STATS["hits"] += 1
        return cached
    _DERIVED_KEY_CACHE_STATS["misses"] += 1
    encryption_key = hashlib.pbkdf2_hmac(
        "sha512", password, salt, parameters.kdf_iter, 32
    )
    hmac_salt = bytes(value ^ 0x3A for value in salt)
    hmac_key = hashlib.pbkdf2_hmac(
        "sha512",
        encryption_key,
        hmac_salt,
        parameters.fast_kdf_iter,
        32,
    )
    if len(_DERIVED_KEY_CACHE) >= _DERIVED_KEY_CACHE_LIMIT:
        _DERIVED_KEY_CACHE.clear()
    _DERIVED_KEY_CACHE[cache_key] = (encryption_key, hmac_key)
    return encryption_key, hmac_key


def _page_hmac(
    encrypted_page: bytes,
    *,
    page_number: int,
    hmac_key: bytes,
    parameters: SQLCipherParametersV1,
) -> bytes:
    content_start = 16 if page_number == 1 else 0
    authenticated = encrypted_page[content_start : -parameters.hmac_size]
    page_number_bytes = page_number.to_bytes(4, "little")
    return hmac.new(hmac_key, authenticated + page_number_bytes, hashlib.sha512).digest()


def _decrypt_page(
    encrypted_page: bytes,
    *,
    page_number: int,
    encryption_key: bytes,
    hmac_key: bytes,
    parameters: SQLCipherParametersV1,
    verify_hmac: bool,
) -> bytes:
    if len(encrypted_page) != parameters.page_size:
        raise SaveDecryptError(f"page {page_number} has an invalid size")

    reserve_start = parameters.page_size - parameters.reserve_size
    content_start = 16 if page_number == 1 else 0
    ciphertext = encrypted_page[content_start:reserve_start]
    iv = encrypted_page[reserve_start : reserve_start + AES.block_size]
    if not ciphertext or len(ciphertext) % AES.block_size:
        raise SaveDecryptError(f"page {page_number} is not AES block aligned")

    if verify_hmac:
        stored_hmac = encrypted_page[-parameters.hmac_size :]
        calculated_hmac = _page_hmac(
            encrypted_page,
            page_number=page_number,
            hmac_key=hmac_key,
            parameters=parameters,
        )
        if not hmac.compare_digest(stored_hmac, calculated_hmac):
            raise SaveDecryptError(
                f"page {page_number} header/authentication validation failed"
            )

    body = AES.new(encryption_key, AES.MODE_CBC, iv).decrypt(ciphertext)
    if page_number == 1:
        header_matches = (
            body[0:2] == parameters.page_size.to_bytes(2, "big")
            and body[2:4] == b"\x01\x01"
            and body[5:8] == b"\x40\x20\x20"
        )
        if not header_matches:
            raise SaveDecryptError("page 1 header/authentication validation failed")
        return b"SQLite format 3\x00" + body + bytes(parameters.reserve_size)
    return body + bytes(parameters.reserve_size)


def decrypt_sqlcipher_bytes(
    encrypted: bytes,
    password: bytes,
    *,
    parameters: SQLCipherParametersV1 = DEFAULT_PARAMETERS,
    verify_hmac: bool = False,
) -> bytes:
    """Decrypt a complete SQLCipher database into SQLite page bytes."""

    if len(encrypted) < parameters.page_size or len(encrypted) % parameters.page_size:
        raise SaveDecryptError("encrypted database size is not page aligned")
    salt = encrypted[:16]
    encryption_key, hmac_key = _derive_keys(password, salt, parameters)
    pages = []
    for offset in range(0, len(encrypted), parameters.page_size):
        page_number = offset // parameters.page_size + 1
        page = encrypted[offset : offset + parameters.page_size]
        pages.append(
            _decrypt_page(
                page,
                page_number=page_number,
                encryption_key=encryption_key,
                hmac_key=hmac_key,
                parameters=parameters,
                verify_hmac=verify_hmac,
            )
        )
    return b"".join(pages)


def apply_encrypted_wal(
    plaintext_database: bytes,
    encrypted_wal: bytes,
    *,
    password: bytes,
    database_salt: bytes,
    parameters: SQLCipherParametersV1 = DEFAULT_PARAMETERS,
    verify_hmac: bool = False,
) -> tuple[bytes, int]:
    """Decrypt WAL frame pages and apply the latest frame for each page."""

    if not encrypted_wal:
        return plaintext_database, 0
    if len(encrypted_wal) < 32:
        raise SaveDecryptError("WAL is shorter than its 32-byte header")
    magic, _version, wal_page_size = struct.unpack(">III", encrypted_wal[:12])
    if magic not in {0x377F0682, 0x377F0683}:
        raise SaveDecryptError("WAL header magic is invalid")
    if wal_page_size == 1:
        wal_page_size = 65536
    if wal_page_size != parameters.page_size:
        raise SaveDecryptError("WAL page size does not match the database")

    frame_size = 24 + parameters.page_size
    frame_bytes = encrypted_wal[32:]
    if len(frame_bytes) % frame_size:
        raise SaveDecryptError("WAL contains a truncated frame")

    encryption_key, hmac_key = _derive_keys(password, database_salt, parameters)
    merged = bytearray(plaintext_database)
    applied = 0
    committed_page_count: int | None = None
    for offset in range(0, len(frame_bytes), frame_size):
        frame = frame_bytes[offset : offset + frame_size]
        page_number, database_size = struct.unpack(">II", frame[:8])
        if page_number < 1:
            raise SaveDecryptError("WAL frame page number is invalid")
        encrypted_page = frame[24:]
        plaintext_page = _decrypt_page(
            encrypted_page,
            page_number=page_number,
            encryption_key=encryption_key,
            hmac_key=hmac_key,
            parameters=parameters,
            verify_hmac=verify_hmac,
        )
        required_size = page_number * parameters.page_size
        if len(merged) < required_size:
            merged.extend(bytes(required_size - len(merged)))
        page_offset = (page_number - 1) * parameters.page_size
        merged[page_offset : page_offset + parameters.page_size] = plaintext_page
        applied += 1
        if database_size:
            committed_page_count = database_size

    if committed_page_count is not None:
        del merged[committed_page_count * parameters.page_size :]
    return bytes(merged), applied


def _validate_sqlite(path: Path) -> None:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SaveDecryptError("decrypted output is not readable SQLite") from exc
    if result != ("ok",):
        raise SaveDecryptError("decrypted SQLite integrity check failed")


def decrypt_sqlcipher_file(
    encrypted_path: Path,
    output_path: Path,
    *,
    password: bytes,
    wal_path: Path | None = None,
    parameters: SQLCipherParametersV1 = DEFAULT_PARAMETERS,
    verify_hmac: bool = False,
) -> OfflineDecryptResultV1:
    """Decrypt one copied database and optionally merge its encrypted WAL."""

    try:
        encrypted = encrypted_path.read_bytes()
    except OSError as exc:
        raise SaveDecryptError("encrypted database could not be read") from exc
    plaintext = decrypt_sqlcipher_bytes(
        encrypted,
        password,
        parameters=parameters,
        verify_hmac=verify_hmac,
    )
    wal_frames = 0
    if wal_path is not None and wal_path.is_file() and wal_path.stat().st_size:
        try:
            encrypted_wal = wal_path.read_bytes()
        except OSError as exc:
            raise SaveDecryptError("encrypted WAL could not be read") from exc
        plaintext, wal_frames = apply_encrypted_wal(
            plaintext,
            encrypted_wal,
            password=password,
            database_salt=encrypted[:16],
            parameters=parameters,
            verify_hmac=verify_hmac,
        )

    if output_path.exists():
        raise SaveDecryptError("refusing to overwrite a plaintext output")
    if not output_path.parent.is_dir():
        raise SaveDecryptError("plaintext output directory does not exist")
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(plaintext)
        _validate_sqlite(temporary)
        os.replace(temporary, output_path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SaveDecryptError("plaintext output could not be written") from exc
    except SaveDecryptError:
        temporary.unlink(missing_ok=True)
        raise

    return OfflineDecryptResultV1(
        output_path=str(output_path),
        encrypted_sha256=hashlib.sha256(encrypted).hexdigest(),
        page_count=len(plaintext) // parameters.page_size,
        wal_frames=wal_frames,
        wal_applied=wal_frames > 0,
    )


__all__ = [
    "DEFAULT_PARAMETERS",
    "OfflineDecryptResultV1",
    "SQLCipherParametersV1",
    "SaveDecryptError",
    "SaveKeyMissingError",
    "apply_encrypted_wal",
    "clear_derived_key_cache",
    "decrypt_sqlcipher_bytes",
    "decrypt_sqlcipher_file",
    "derived_key_cache_stats",
    "load_save_password",
]
