"""Reviewstage LAN URL resolution and submit-host gateway preflight.

This module deliberately distinguishes two claims:

* a material is self-contained enough to survive a remote browser; and
* the configured LAN gateway can serve that material from the submitting host.

The second check cannot prove that a physical phone or another workstation is
allowed through the host firewall, so every result records its probe scope.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


_DEFAULT_GATEWAY_PORT = 12443
_MATERIAL_PATH = "/api/boss-sight/reviewstage/{material_id}/file"
_REMOTE_RECEIPT_MAX_AGE_SECONDS = 10 * 60
_PHYSICAL_REMOTE_SCOPES = frozenset({"external_remote_probe", "physical_remote_device"})


def _primary_lan_ipv4() -> str | None:
    """Best-effort RFC1918 IPv4 used for the machine's default route."""

    candidates: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect selects a route without sending application data.
        sock.connect(("1.1.1.1", 80))
        candidates.append(str(sock.getsockname()[0]))
    except OSError:
        pass
    finally:
        sock.close()

    try:
        candidates.extend(
            str(item[4][0])
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass

    for raw in candidates:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if addr.version == 4 and addr.is_private and not addr.is_loopback and not addr.is_link_local:
            return str(addr)
    return None


def _registered_gateway_port(workspace_root: Path) -> int | None:
    """Read the registered human-facing HTTPS gateway without adding a YAML dep."""

    registry = workspace_root / "config" / "ports.yaml"
    try:
        text = registry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Pick the human-facing HTTPS entry, not the later codeweb HTTP :80 entry.
    # Keep the expression line-bounded: ``\s`` across YAML blocks previously let
    # the first port capture backtrack all the way to a later owner/service pair.
    match = re.search(
        r"(?mi)^gateways:[^\S\r\n]*\r?\n"
        r"[^\S\r\n]*-[^\S\r\n]*port:[^\S\r\n]*(\d+)[^\S\r\n]*\r?\n"
        r"[^\S\r\n]+owner:[^\S\r\n]*codeweb[^\S\r\n]*\r?\n"
        r"[^\S\r\n]+service:[^\r\n]*HTTPS",
        text,
    )
    return int(match.group(1)) if match else None


def resolve_review_remote_base(workspace_root: Path) -> str | None:
    """Return the canonical LAN-facing dashboard base, or None if unconfigured."""

    explicit = (
        os.environ.get("OMNI_REVIEW_REMOTE_URL")
        or os.environ.get("OMNI_DASHBOARD_REMOTE_URL")
        or ""
    ).strip()
    if explicit:
        return explicit.rstrip("/")

    port = _registered_gateway_port(Path(workspace_root))
    if port is None:
        return None
    host = _primary_lan_ipv4()
    if not host:
        return None
    return f"https://{host}:{port or _DEFAULT_GATEWAY_PORT}"


def review_remote_material_url(material_id: str, workspace_root: Path) -> str | None:
    base = resolve_review_remote_base(workspace_root)
    if not base:
        return None
    return f"{base}{_MATERIAL_PATH.format(material_id=material_id)}"


def review_remote_material_open_url(material_id: str, workspace_root: Path) -> str | None:
    """Return the LAN-facing cockpit/Dockview link for a material."""

    from .links import review_material_open_url

    base = resolve_review_remote_base(workspace_root)
    if not base:
        return None
    return review_material_open_url(base, material_id)


def _is_loopback_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    if host in {"localhost", "0.0.0.0", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _fresh_timestamp(value: Any, *, now: datetime, max_age_seconds: int) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    age = (now - parsed.astimezone(timezone.utc)).total_seconds()
    return -30 <= age <= max_age_seconds


def _record_has_warnings(record: Mapping[str, Any]) -> bool:
    if isinstance(record.get("warning"), Mapping):
        return True
    warnings = record.get("warnings")
    return isinstance(warnings, list) and bool(warnings)


def remote_push_blockers(
    *,
    material_id: str,
    remote_preflight: Mapping[str, Any] | None,
    remote_verification: Mapping[str, Any] | None,
    now: datetime | None = None,
    max_age_seconds: int = _REMOTE_RECEIPT_MAX_AGE_SECONDS,
) -> list[str]:
    """Return advisory reasons that would previously have blocked a push.

    2026-07-25 用户裁定：push 硬门禁去除，降级为提示。这些 blocker 只作为
    advisory 记录进 push 历史和输出，不再阻止 ``mark_pushed``。

    ``remote_preflight`` is the live submit-host -> gateway diagnostic. It is
    necessary but never counts as proof from a physical remote device.
    ``remote_verification`` is a separate, short-lived receipt produced by an
    external probe or physical device. Keeping the records separate prevents an
    agent from relabelling a same-host HTTP 200 as remote verification.
    """

    checked_at = now or datetime.now(timezone.utc)
    blockers: list[str] = []

    host = remote_preflight if isinstance(remote_preflight, Mapping) else {}
    host_url = str(host.get("remote_url") or "").strip()
    if not host:
        blockers.append("remote_preflight_missing")
    else:
        if host.get("status") != "pass":
            blockers.append("remote_preflight_not_pass")
        if host.get("scope") != "gateway_from_submit_host":
            blockers.append("remote_preflight_scope_invalid")
        if not _fresh_timestamp(
            host.get("checked_at"),
            now=checked_at,
            max_age_seconds=max_age_seconds,
        ):
            blockers.append("remote_preflight_stale")
        if host.get("http_status") not in {200, 206}:
            blockers.append("remote_http_status_invalid")
        if host.get("identity_ok") is not True:
            blockers.append("remote_identity_unverified")
        if host.get("tls_verified") is not True:
            blockers.append("remote_tls_unverified")
        if _record_has_warnings(host):
            blockers.append("remote_preflight_has_warnings")
    if not host_url:
        blockers.append("remote_url_missing")
    elif _is_loopback_url(host_url):
        blockers.append("remote_url_loopback")

    physical = remote_verification if isinstance(remote_verification, Mapping) else {}
    if not physical:
        blockers.append("physical_remote_verification_missing")
    else:
        if physical.get("status") != "pass":
            blockers.append("physical_remote_verification_not_pass")
        if physical.get("scope") not in _PHYSICAL_REMOTE_SCOPES:
            blockers.append("physical_remote_verification_scope_invalid")
        if physical.get("physical_remote_verified") is not True:
            blockers.append("physical_remote_unverified")
        if str(physical.get("material_id") or "") != material_id:
            blockers.append("physical_remote_material_mismatch")
        physical_url = str(physical.get("remote_url") or "").strip()
        if not host_url or physical_url != host_url:
            blockers.append("physical_remote_url_mismatch")
        if physical.get("http_status") not in {200, 206}:
            blockers.append("physical_remote_http_status_invalid")
        if physical.get("identity_ok") is not True:
            blockers.append("physical_remote_identity_unverified")
        if physical.get("tls_verified") is not True:
            blockers.append("physical_remote_tls_unverified")
        if not str(physical.get("verifier") or "").strip():
            blockers.append("physical_remote_verifier_missing")
        if not _fresh_timestamp(
            physical.get("verified_at"),
            now=checked_at,
            max_age_seconds=max_age_seconds,
        ):
            blockers.append("physical_remote_verification_stale")
        if _record_has_warnings(physical):
            blockers.append("physical_remote_verification_has_warnings")

    return blockers


def _identity_ok(kind: str, content_type: str, sample: bytes) -> bool:
    if not sample:
        return False
    kind = str(kind or "").lower()
    ctype = str(content_type or "").lower()
    lower = sample[:4096].lower()
    if kind in {"html", "static-report", "demo"}:
        return b"<!doctype" in lower or b"<html" in lower or "text/html" in ctype
    if kind == "image":
        return ctype.startswith("image/") or sample.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF"))
    if kind == "video":
        return ctype.startswith("video/") or b"ftyp" in sample[:64]
    return bool(sample.strip())


def _open_sample(url: str, *, timeout: float, verify_tls: bool) -> tuple[int, str, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "Range": "bytes=0-4095",
            "User-Agent": "omni-review-submit-preflight/1",
        },
    )
    context = None
    if url.lower().startswith("https://") and not verify_tls:
        context = ssl._create_unverified_context()  # noqa: SLF001 - diagnostic fallback only
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        status = int(getattr(response, "status", 200))
        content_type = str(response.headers.get("Content-Type") or "")
        return status, content_type, response.read(4096)


def _warning(code: str, message: str, *, path: str = "remote_url") -> dict[str, str]:
    return {"code": code, "severity": "warning", "message": message, "path": path}


def _windows_inbound_risk(url: str, *, timeout: float = 2.0) -> dict[str, str] | None:
    """Warn when Windows' active policy is a likely physical-LAN blocker.

    A same-host request bypasses the decisive inbound edge. This diagnostic catches
    the exact false-green case where the current profile blocks inbound traffic and
    neither a TCP-port rule nor a Caddy application rule is active.
    """

    if os.name != "nt":
        return None
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        profile = subprocess.run(
            ["netsh", "advfirewall", "show", "currentprofile"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    profile_text = profile.stdout or ""
    if profile.returncode != 0 or "BlockInbound" not in profile_text:
        return None

    script = (
        "$rules=Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True "
        "-Direction Inbound -Action Allow -ErrorAction SilentlyContinue;"
        f"$ports=@($rules | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.Protocol -eq 'TCP' -and \"$($_.LocalPort)\" -eq '{port}' }}).Count;"
        "$apps=@($rules | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Program -match 'caddy|codeweb' }).Count;"
        "[Console]::Write(\"$ports,$apps\")"
    )
    try:
        active = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
        port_count, app_count = (int(part) for part in active.stdout.strip().split(",", 1))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if active.returncode == 0 and (port_count > 0 or app_count > 0):
        return None

    gpo_only = bool(re.search(r"(?mi)^LocalFirewallRules\s+N/A", profile_text))
    policy_note = "，且本地规则被 GPO 禁用" if gpo_only else ""
    return _warning(
        "remote_firewall_inbound_unallowed",
        f"Windows 当前防火墙策略阻断入站{policy_note}，未发现 TCP {port} 或 Caddy 的有效放行规则；"
        "本机/WSL 预检通过仍不能让另一台设备连入，需要系统或公司策略放行。",
        path="windows_firewall",
    )


def _windows_tls_risk(url: str, *, timeout: float = 2.0) -> dict[str, str] | None:
    """Use Windows Schannel as a browser-adjacent TLS trust check."""

    if os.name != "nt" or not url.lower().startswith("https://"):
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        probe = subprocess.run(
            [
                "curl.exe",
                "--silent",
                "--show-error",
                "--range",
                "0-0",
                "--output",
                "NUL",
                "--write-out",
                "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode == 0 and probe.stdout.strip() in {"200", "206"}:
        return None
    diagnostic = (probe.stderr or "").strip().replace("\r", " ").replace("\n", " ")
    diagnostic = diagnostic.encode("ascii", errors="ignore").decode("ascii")
    tls_failure = probe.returncode in {35, 51, 58, 60} or bool(
        re.search(r"(?i)schannel|certificate|ssl|tls|CRYPT_E_", diagnostic)
    )
    if not tls_failure:
        return None
    return _warning(
        "remote_gateway_windows_tls_untrusted",
        "Python 探针能读取正式 HTTPS 网关，但 Windows/浏览器证书通道未能验证它"
        f"（{diagnostic[:220] or f'curl exit {probe.returncode}'}）；远程设备可能需安装 codeweb 根证书。",
        path="remote_url",
    )


def preflight_material_remote_access(
    *,
    material_id: str,
    kind: str,
    workspace_root: Path,
    timeout: float = 2.0,
    remote_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Probe the canonical gateway and return a persistable diagnostic record."""

    url = review_remote_material_url(material_id, workspace_root)
    result: dict[str, Any] = {
        "status": "unconfigured",
        "remote_url": url,
        "scope": "gateway_from_submit_host",
        "physical_remote_verified": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "warning": None,
        "warnings": [],
    }
    if not url:
        result["warning"] = _warning(
            "remote_gateway_unconfigured",
            "未发现审阅台远程网关；提交已完成，但没有可供局域网设备打开的正式链接。",
        )
        return result
    if _is_loopback_url(url):
        result["status"] = "warning"
        result["warning"] = _warning(
            "remote_gateway_is_loopback",
            f"远程审阅链接仍指向本机回环地址：{url}",
        )
        return result

    try:
        status, content_type, sample = _open_sample(url, timeout=timeout, verify_tls=True)
        tls_verified = True
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        cert_error = isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc)
        if not cert_error:
            result["status"] = "failed"
            result["error"] = f"{type(reason or exc).__name__}: {reason or exc}"
            result["warning"] = _warning(
                "remote_gateway_unreachable",
                f"提交主机无法通过正式局域网网关读取材料：{url}（{result['error']}）。",
            )
            return result
        try:
            status, content_type, sample = _open_sample(url, timeout=timeout, verify_tls=False)
        except (urllib.error.URLError, OSError, TimeoutError) as retry_exc:
            result["status"] = "failed"
            result["error"] = f"{type(retry_exc).__name__}: {retry_exc}"
            result["warning"] = _warning(
                "remote_gateway_unreachable",
                f"正式局域网网关不可用：{url}（{result['error']}）。",
            )
            return result
        tls_verified = False
    except (OSError, TimeoutError) as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["warning"] = _warning(
            "remote_gateway_unreachable",
            f"提交主机无法通过正式局域网网关读取材料：{url}（{result['error']}）。",
        )
        return result

    identity_ok = status in {200, 206} and _identity_ok(kind, content_type, sample)
    result.update(
        {
            "http_status": status,
            "content_type": content_type,
            "identity_ok": identity_ok,
            "tls_verified": tls_verified,
        }
    )
    if not identity_ok:
        result["status"] = "failed"
        result["warning"] = _warning(
            "remote_material_identity_failed",
            f"远程网关有响应，但返回体不像 kind={kind} 的材料：{url}",
        )
    elif not tls_verified:
        base = resolve_review_remote_base(workspace_root) or ""
        result["status"] = "warning"
        result["ca_url"] = f"{base}/codeweb-root-CA.crt"
        result["warning"] = _warning(
            "remote_gateway_tls_untrusted",
            "正式网关可读取材料，但当前 TLS 证书未被验证；远程设备需信任 codeweb 根证书。",
        )
        result["warnings"].append(result["warning"])
    else:
        result["status"] = "pass"

    # A current, device-token-authenticated receipt for these exact bytes and URL
    # is stronger evidence than Windows' policy heuristics. It does not replace
    # this submit-host probe; it only resolves the two diagnostics whose claimed
    # risk (browser TLS trust / inbound reachability) the phone has just disproved.
    physical_receipt_valid = (
        result.get("status") == "pass"
        and not remote_push_blockers(
            material_id=material_id,
            remote_preflight=result,
            remote_verification=remote_verification,
        )
    )
    if physical_receipt_valid:
        result["diagnostics_reconciled_by"] = "matching_physical_remote_receipt"
    else:
        windows_tls_warning = _windows_tls_risk(url, timeout=timeout)
        if windows_tls_warning:
            result["warnings"].append(windows_tls_warning)
            if result.get("warning") is None:
                result["warning"] = windows_tls_warning
            result["status"] = "warning"
            base = resolve_review_remote_base(workspace_root) or ""
            result["ca_url"] = f"{base}/codeweb-root-CA.crt"

        firewall_warning = _windows_inbound_risk(url, timeout=timeout)
        if firewall_warning:
            result["warnings"].append(firewall_warning)
            if result.get("warning") is None:
                result["warning"] = firewall_warning
            result["status"] = "warning"
    return result


__all__ = [
    "preflight_material_remote_access",
    "remote_push_blockers",
    "resolve_review_remote_base",
    "review_remote_material_open_url",
    "review_remote_material_url",
]
