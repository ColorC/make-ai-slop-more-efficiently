# [OMNI] origin=codex created_by=codex intent="check Romantic Restaurant device and build readiness without game actions"
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "game-observatory.readiness-receipt.v1"
GAME_ID = "romantic-restaurant"
MUMU_MANAGER = Path("C:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe")
MUMU_INSTANCE = "2"
ADB_SERIAL = "127.0.0.1:16448"
PACKAGE_NAME = "com.mergegames.gossipharbor"
EXPECTED_VERSION_NAME = "3.95.0"
EXPECTED_VERSION_CODE = 294
MINIMUM_E_FREE_GB = 20.0

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
DiskUsage = Callable[[str], Any]

_REQUIRED_CHECKS = (
    "mumu.instance_2",
    "adb.serial",
    "package",
    "display",
    "root",
    "disk.E_free_gb",
    "canonical_inspect",
)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _raw(completed: subprocess.CompletedProcess[str]) -> str:
    stdout = _text(completed.stdout)
    stderr = _text(completed.stderr)
    if stdout and stderr:
        separator = "" if stdout.endswith("\n") else "\n"
        return f"{stdout}{separator}{stderr}"
    return stdout or stderr


def _run(runner: Runner, args: list[str]) -> subprocess.CompletedProcess[str]:
    return runner([str(arg) for arg in args])


def _parse_mumu_started(raw: str) -> bool | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("is_android_started"), bool):
        return payload["is_android_started"]
    match = re.search(
        r'["\']?is_android_started["\']?\s*[:=]\s*(true|false|1|0)',
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower() in {"true", "1"}


def _check(
    *,
    status: str,
    value: Any,
    raw: str,
) -> dict[str, Any]:
    return {"status": status, "value": value, "raw": raw}


def _checked_at(now: Callable[[], datetime]) -> str:
    checked = now()
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finalize(receipt: dict[str, Any]) -> dict[str, Any]:
    checks = receipt["checks"]
    receipt["ready_for_p1"] = all(
        checks.get(name, {}).get("status") == "pass" for name in _REQUIRED_CHECKS
    )
    return receipt


def collect_readiness(
    runner: Runner,
    *,
    adb_path: str | Path,
    launch: bool = False,
    disk_usage: DiskUsage = shutil.disk_usage,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    launch_timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 2.0,
    mumu_manager: str | Path = MUMU_MANAGER,
    serial: str = ADB_SERIAL,
    package_name: str = PACKAGE_NAME,
    omni_executable: str = "omni",
) -> dict[str, Any]:
    """Collect a P1 readiness receipt without performing game actions.

    All process execution is delegated to ``runner``. The remaining injectable
    clocks and disk probe keep polling and host-state checks deterministic in tests.
    """

    adb = str(adb_path)
    manager = str(mumu_manager)
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "game_id": GAME_ID,
        "checked_at": _checked_at(now),
        "ready_for_p1": False,
        "checks": checks,
        "blockers": blockers,
    }

    info_results = [_run(runner, [manager, "info", "-v", MUMU_INSTANCE])]
    started = (
        _parse_mumu_started(_raw(info_results[-1]))
        if info_results[-1].returncode == 0
        else None
    )
    launched = False
    waited_seconds = 0.0
    launch_result: subprocess.CompletedProcess[str] | None = None
    if started is not True and launch:
        launch_result = _run(
            runner,
            [manager, "control", "-v", MUMU_INSTANCE, "launch"],
        )
        launched = launch_result.returncode == 0
        while launched and started is not True and waited_seconds < launch_timeout_seconds:
            wait_for = min(poll_interval_seconds, launch_timeout_seconds - waited_seconds)
            if wait_for <= 0:
                break
            sleep(wait_for)
            waited_seconds += wait_for
            current = _run(runner, [manager, "info", "-v", MUMU_INSTANCE])
            info_results.append(current)
            started = _parse_mumu_started(_raw(current)) if current.returncode == 0 else None

    mumu_raw_parts = [_raw(result) for result in info_results]
    if launch_result is not None:
        mumu_raw_parts.insert(1, _raw(launch_result))
    mumu_passed = started is True
    checks["mumu.instance_2"] = _check(
        status="pass" if mumu_passed else "fail",
        value={
            "is_android_started": started,
            "launch_requested": launch,
            "launched": launched,
            "wait_seconds": waited_seconds,
        },
        raw="\n".join(part for part in mumu_raw_parts if part),
    )
    if not mumu_passed:
        blockers.append("MuMu 实例 2 未启动")

    connect_result = _run(runner, [adb, "connect", serial])
    devices_result = _run(runner, [adb, "devices"])
    devices_raw = _raw(devices_result)
    device_match = re.search(
        rf"(?m)^\s*{re.escape(serial)}\s+(\S+)",
        devices_raw,
    )
    device_state = device_match.group(1) if device_match else None
    adb_passed = devices_result.returncode == 0 and device_state == "device"
    checks["adb.serial"] = _check(
        status="pass" if adb_passed else "fail",
        value={"serial": serial, "state": device_state},
        raw="\n".join(part for part in (_raw(connect_result), devices_raw) if part),
    )
    if not adb_passed:
        blockers.append(f"ADB 设备不可用: {serial} 状态为 {device_state or 'missing'}")

    package_result = _run(
        runner,
        [adb, "-s", serial, "shell", "dumpsys", "package", package_name],
    )
    package_raw = _raw(package_result)
    version_name_match = re.search(r"(?m)^\s*versionName=(\S+)", package_raw)
    version_code_match = re.search(r"(?m)^\s*versionCode=(\d+)\b", package_raw)
    version_name = version_name_match.group(1) if version_name_match else None
    version_code = int(version_code_match.group(1)) if version_code_match else None
    package_exists = (
        package_result.returncode == 0
        and "Unable to find package" not in package_raw
        and (version_name is not None or version_code is not None)
    )
    package_passed = (
        package_exists
        and version_name == EXPECTED_VERSION_NAME
        and version_code == EXPECTED_VERSION_CODE
    )
    checks["package"] = _check(
        status="pass" if package_passed else "fail",
        value={
            "name": package_name,
            "exists": package_exists,
            "version_name": version_name,
            "version_code": version_code,
            "expected_version_name": EXPECTED_VERSION_NAME,
            "expected_version_code": EXPECTED_VERSION_CODE,
        },
        raw=package_raw,
    )
    if not package_exists:
        blockers.append(f"package 缺失: {package_name}")
        return _finalize(receipt)
    if not package_passed:
        blockers.append(
            "package 版本不匹配: "
            f"versionName 期望 {EXPECTED_VERSION_NAME} 实际 {version_name}; "
            f"versionCode 期望 {EXPECTED_VERSION_CODE} 实际 {version_code}"
        )
        return _finalize(receipt)

    display_result = _run(runner, [adb, "-s", serial, "shell", "wm", "size"])
    display_raw = _raw(display_result)
    size_match = re.search(r"(\d+)\s*x\s*(\d+)", display_raw)
    width = int(size_match.group(1)) if size_match else None
    height = int(size_match.group(2)) if size_match else None
    resolution = f"{width}x{height}" if width is not None and height is not None else None
    display_passed = display_result.returncode == 0 and resolution in {
        "1080x1920",
        "1920x1080",
    }
    if width is None or height is None:
        orientation = None
    else:
        orientation = "landscape" if width > height else "portrait"
    checks["display"] = _check(
        status="pass" if display_passed else "fail",
        value={"resolution": resolution, "orientation": orientation},
        raw=display_raw,
    )
    if not display_passed:
        blockers.append(f"显示分辨率不匹配: 实际 {resolution or 'unknown'}")

    su_result = _run(runner, [adb, "-s", serial, "shell", "su", "-c", "id"])
    root_results = [su_result]
    root_output = _raw(su_result)
    root_passed = su_result.returncode == 0 and "uid=0" in root_output
    root_method = "su -c id"
    if not root_passed:
        adb_root_result = _run(runner, [adb, "-s", serial, "root"])
        id_result = _run(runner, [adb, "-s", serial, "shell", "id"])
        root_results.extend((adb_root_result, id_result))
        root_output = _raw(id_result)
        root_passed = id_result.returncode == 0 and "uid=0" in root_output
        root_method = "adb root + adb shell id"
    checks["root"] = _check(
        status="pass" if root_passed else "fail",
        value={"uid_0": root_passed, "method": root_method},
        raw="\n".join(_raw(result) for result in root_results if _raw(result)),
    )
    if not root_passed:
        blockers.append("设备 root 不可用: id 输出不含 uid=0")

    frida_ls_result = _run(
        runner,
        [adb, "-s", serial, "shell", "ls /data/local/tmp/ | grep -i frida"],
    )
    frida_pgrep_result = _run(
        runner,
        [adb, "-s", serial, "shell", "pgrep", "-f", "frida-server"],
    )
    frida_ls_raw = _raw(frida_ls_result)
    frida_pgrep_raw = _raw(frida_pgrep_result)
    checks["frida_server"] = _check(
        status="info",
        value={
            "present": frida_ls_result.returncode == 0 and bool(frida_ls_raw.strip()),
            "running": frida_pgrep_result.returncode == 0 and bool(frida_pgrep_raw.strip()),
        },
        raw="\n".join(part for part in (frida_ls_raw, frida_pgrep_raw) if part),
    )

    legacy_dir_match = re.search(r"(?m)^\s*legacyNativeLibraryDir=(\S+)", package_raw)
    primary_abi_match = re.search(r"(?m)^\s*primaryCpuAbi=(\S+)", package_raw)
    legacy_native_library_dir = legacy_dir_match.group(1) if legacy_dir_match else None
    primary_cpu_abi = primary_abi_match.group(1) if primary_abi_match else None

    native_dir_listing: list[str] | str = "unavailable"
    native_listing_raw = ""
    if legacy_native_library_dir:
        native_listing_result = _run(
            runner,
            [
                adb,
                "-s",
                serial,
                "shell",
                "ls",
                "-R",
                legacy_native_library_dir,
            ],
        )
        native_listing_raw = _raw(native_listing_result)
        if native_listing_result.returncode == 0:
            native_dir_listing = [
                line.strip() for line in native_listing_raw.splitlines() if line.strip()
            ]

    pm_path_result = _run(
        runner,
        [adb, "-s", serial, "shell", "pm", "path", package_name],
    )
    pm_path_raw = _raw(pm_path_result)
    apk_paths = re.findall(r"(?m)^package:(\S+)", pm_path_raw)
    base_apk_path = next(
        (path for path in apk_paths if path.endswith("/base.apk")),
        apk_paths[0] if apk_paths else None,
    )

    so_in_apk: list[str] | str = "unavailable"
    unzip_raw = ""
    if base_apk_path:
        unzip_result = _run(
            runner,
            [
                adb,
                "-s",
                serial,
                "shell",
                "su",
                "-c",
                f"unzip -l {base_apk_path} | grep -E 'lib/.*\\.so$'",
            ],
        )
        unzip_raw = _raw(unzip_result)
        unzip_unavailable = unzip_result.returncode == 127 or bool(
            re.search(
                r"(?:unzip|su).*(?:not found|inaccessible)",
                unzip_raw,
                re.IGNORECASE,
            )
        )
        if not unzip_unavailable:
            so_in_apk = []
            for line in unzip_raw.splitlines():
                match = re.search(r"(lib/\S+\.so)\s*$", line)
                if match:
                    so_in_apk.append(match.group(1))

    discovered_libraries = f"{native_listing_raw}\n{unzip_raw}"
    checks["xlua_lib"] = _check(
        status="info",
        value={
            "legacy_native_library_dir": legacy_native_library_dir,
            "primary_cpu_abi": primary_cpu_abi,
            "native_dir_listing": native_dir_listing,
            "base_apk_path": base_apk_path,
            "so_in_apk": so_in_apk,
            "libxlua.so": "libxlua.so" in discovered_libraries,
            "libil2cpp.so": "libil2cpp.so" in discovered_libraries,
            "libunity.so": "libunity.so" in discovered_libraries,
        },
        raw="\n".join(
            part
            for part in (
                native_listing_raw,
                pm_path_raw,
                unzip_raw,
            )
            if part
        ),
    )

    usage = disk_usage("E:/")
    free_gb = usage.free / (1024**3)
    disk_passed = free_gb >= MINIMUM_E_FREE_GB
    checks["disk.E_free_gb"] = _check(
        status="pass" if disk_passed else "fail",
        value=round(free_gb, 3),
        raw=f"free_bytes={usage.free}",
    )
    if not disk_passed:
        blockers.append(
            f"E 盘剩余空间不足: {free_gb:.3f} GB，要求至少 {MINIMUM_E_FREE_GB:g} GB"
        )

    inspect_result = _run(
        runner,
        [
            omni_executable,
            "game",
            "player",
            "--json",
            "device",
            "inspect",
            serial,
            "--package",
            package_name,
        ],
    )
    canonical_passed = inspect_result.returncode == 0
    checks["canonical_inspect"] = _check(
        status="pass" if canonical_passed else "fail",
        value={"returncode": inspect_result.returncode},
        raw=_raw(inspect_result),
    )
    if not canonical_passed:
        blockers.append(f"canonical inspect 失败: exit {inspect_result.returncode}")

    return _finalize(receipt)


def receipt_exit_code(receipt: dict[str, Any]) -> int:
    return 0 if receipt.get("ready_for_p1") is True else 2


def write_receipt(receipt: dict[str, Any], output_directory: str | Path) -> Path:
    checked_at = str(receipt["checked_at"])
    filename_timestamp = re.sub(r"[^0-9A-Za-z]", "", checked_at)
    path = Path(output_directory) / f"{filename_timestamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
