# [OMNI] material_id="material:core.agent.resource-control.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="模型资源控制与证据化互评：重试去重、append-only 纠正及同任务样本门槛回流"
# [OMNI] tags=agent-control,peer-review,routing,evidence
"""Provider quota observations, model routing and cross-session peer reviews.

The control plane keeps facts separate from assumptions: usage pages and reset
rules are query methods, while account-specific balances are timestamped
observations.  Read-only status never probes CLIs or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable
import urllib.error
import urllib.request
import uuid

from omnicompany.core.config import omni_workspace_root


SCHEMA = "omnicompany.agent-resource-state.v1"
REVIEW_SCHEMA = "omnicompany.agent-peer-review.v1"
REVIEW_AMENDMENT_SCHEMA = "omnicompany.agent-peer-review-amendment.v1"
VALID_SEVERITIES = {"pass", "imperfect", "severe"}
TASK_ALIASES = {
    "front-end": "frontend",
    "ui": "frontend",
    "visual": "multimodal",
    "vision": "multimodal",
    "chinese": "chinese_writing",
    "writing_zh": "chinese_writing",
    "long-run": "long_running",
    "test": "testing",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def default_catalog_path() -> Path:
    return omni_workspace_root() / "config" / "agent_control" / "catalog.json"


def default_state_dir() -> Path:
    override = os.environ.get("OMNI_AGENT_CONTROL_STATE_DIR")
    return Path(override) if override else omni_workspace_root() / "data" / "llm" / "agent_control"


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    source = path or default_catalog_path()
    data = json.loads(source.read_text(encoding="utf-8"))
    providers = data.get("providers") or []
    models = data.get("models") or []
    provider_ids = [str(row.get("id") or "") for row in providers]
    model_ids = [str(row.get("id") or "") for row in models]
    if not providers or len(provider_ids) != len(set(provider_ids)) or "" in provider_ids:
        raise ValueError("agent control catalog has missing or duplicate provider ids")
    if not models or len(model_ids) != len(set(model_ids)) or "" in model_ids:
        raise ValueError("agent control catalog has missing or duplicate model ids")
    invalid_tiers = sorted({row.get("tier") for row in models} - {"T1", "T2", "T3", "T4"})
    if invalid_tiers:
        raise ValueError(f"invalid model tiers: {invalid_tiers}")
    unknown = sorted(
        {
            provider
            for row in models
            for provider in row.get("providers") or []
            if provider not in set(provider_ids)
        }
    )
    if unknown:
        raise ValueError(f"models reference unknown providers: {unknown}")
    return data


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def peer_review_fingerprint(row: dict[str, Any]) -> str:
    """Return a stable identity for one semantic peer review.

    Transport fields (id/timestamp) are intentionally excluded so a retried CLI
    write does not become a second performance sample.
    """
    payload = {
        "reviewer_session": str(row.get("reviewer_session") or ""),
        "reviewer_model": str(row.get("reviewer_model") or ""),
        "reviewer_provider": str(row.get("reviewer_provider") or ""),
        "subject_session": str(row.get("subject_session") or ""),
        "subject_model": str(row.get("subject_model") or ""),
        "subject_provider": str(row.get("subject_provider") or ""),
        "severity": str(row.get("severity") or ""),
        "task_type": str(row.get("task_type") or "general"),
        "issues": sorted(str(item).strip() for item in row.get("issues") or [] if str(item).strip()),
        "upstream_factors": sorted(
            str(item).strip() for item in row.get("upstream_factors") or [] if str(item).strip()
        ),
        "evidence": sorted(str(item).strip() for item in row.get("evidence") or [] if str(item).strip()),
        "note": str(row.get("note") or "").strip(),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "review_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deduplicate_peer_reviews(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the first semantic review and report how many retry rows were ignored."""
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    for row in rows:
        fingerprint = str(row.get("fingerprint") or peer_review_fingerprint(row))
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        clean = dict(row)
        clean.setdefault("fingerprint", fingerprint)
        unique.append(clean)
    return unique, duplicates


def peer_review_amendment_fingerprint(row: dict[str, Any]) -> str:
    """Return a stable identity for one append-only review correction link."""
    payload = {
        "actor_id": str(row.get("actor_id") or "").strip(),
        "superseded_review_ids": sorted(
            str(item).strip() for item in row.get("superseded_review_ids") or [] if str(item).strip()
        ),
        "replacement_review_id": str(row.get("replacement_review_id") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
        "evidence": sorted(
            str(item).strip() for item in row.get("evidence") or [] if str(item).strip()
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "review_amendment_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_peer_reviews(
    rows: Iterable[dict[str, Any]],
    amendments: Iterable[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], int, int]:
    """Resolve retry duplicates and valid correction links into the current review state.

    Amendments never rewrite history.  Invalid/stale links are ignored so a malformed
    maintenance row cannot suppress an unrelated review.
    """
    unique, duplicates = deduplicate_peer_reviews(rows)
    by_id = {str(row.get("id") or ""): row for row in unique if row.get("id")}
    superseded: set[str] = set()
    for amendment in amendments:
        if amendment.get("schema") != REVIEW_AMENDMENT_SCHEMA:
            continue
        replacement_id = str(amendment.get("replacement_review_id") or "")
        replacement = by_id.get(replacement_id)
        if replacement is None:
            continue
        cohort = (
            str(replacement.get("subject_session") or ""),
            str(replacement.get("subject_model") or ""),
            str(replacement.get("task_type") or "general"),
        )
        candidates = [str(item) for item in amendment.get("superseded_review_ids") or []]
        if not candidates or any(review_id == replacement_id for review_id in candidates):
            continue
        prior_rows = [by_id.get(review_id) for review_id in candidates]
        if any(row is None for row in prior_rows):
            continue
        if any(
            (
                str(row.get("subject_session") or ""),
                str(row.get("subject_model") or ""),
                str(row.get("task_type") or "general"),
            )
            != cohort
            for row in prior_rows
            if row is not None
        ):
            continue
        superseded.update(candidates)
    return [row for row in unique if str(row.get("id") or "") not in superseded], duplicates, len(superseded)


def _provider_index(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in catalog["providers"]}


def _model_index(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in catalog["models"]}


class ResourceControl:
    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        state_dir: Path | None = None,
        hr_data_dir: Path | None = None,
    ) -> None:
        self.catalog_path = catalog_path or default_catalog_path()
        self.state_dir = state_dir or default_state_dir()
        self.state_path = self.state_dir / "provider_resources.json"
        self.news_path = self.state_dir / "official_news.json"
        self.review_path = self.state_dir / "peer_reviews.jsonl"
        self.review_amendment_path = self.state_dir / "peer_review_amendments.jsonl"
        self.hr_data_dir = (
            Path(hr_data_dir)
            if hr_data_dir is not None
            else (Path(state_dir).parent / "hr" if state_dir is not None else omni_workspace_root() / "data" / "hr")
        )
        self.catalog = load_catalog(self.catalog_path)

    def _state(self) -> dict[str, Any]:
        return _read_json(self.state_path, {"schema": SCHEMA, "providers": {}})

    def _write_state(self, state: dict[str, Any]) -> None:
        state["schema"] = SCHEMA
        state["updated_at"] = _iso_now()
        _atomic_json(self.state_path, state)

    def observe(
        self,
        provider: str,
        *,
        remaining: float,
        capacity: float | None = None,
        unit: str = "tokens",
        reset_at: str | None = None,
        window_id: str = "primary",
        account: str = "default",
        source: str,
        evidence: str = "",
        confidence: str = "reported",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        providers = _provider_index(self.catalog)
        if provider not in providers:
            raise ValueError(f"unknown provider: {provider}")
        if remaining < 0 or (capacity is not None and capacity < 0):
            raise ValueError("remaining/capacity must be non-negative")
        if capacity is not None and remaining > capacity:
            raise ValueError("remaining cannot exceed capacity")
        if reset_at and _parse_time(reset_at) is None:
            raise ValueError("reset_at must be ISO-8601")
        timestamp = observed_at or _iso_now()
        if _parse_time(timestamp) is None:
            raise ValueError("observed_at must be ISO-8601")
        row = {
            "provider": provider,
            "account": account,
            "window_id": window_id,
            "remaining": float(remaining),
            "capacity": None if capacity is None else float(capacity),
            "unit": unit,
            "reset_at": reset_at,
            "observed_at": timestamp,
            "source": source,
            "evidence": evidence,
            "confidence": confidence,
        }
        state = self._state()
        bucket = state.setdefault("providers", {}).setdefault(provider, {})
        observations = list(bucket.get("observations") or [])
        previous = next(
            (
                item
                for item in reversed(observations)
                if item.get("window_id") == window_id and item.get("account") == account
            ),
            None,
        )
        observations.append(row)
        bucket["observations"] = observations[-200:]
        if previous and str(previous.get("unit")) == unit:
            delta = float(previous.get("remaining") or 0) - float(row["remaining"])
            row["remaining_delta"] = round(delta, 6)
            row["consumed_since_previous"] = round(max(0.0, delta), 6)
            row["credited_since_previous"] = round(max(0.0, -delta), 6)
            previous_time = _parse_time(previous.get("observed_at"))
            current_time = _parse_time(timestamp)
            row["elapsed_hours"] = (
                None
                if not previous_time or not current_time
                else round((current_time - previous_time).total_seconds() / 3600, 4)
            )
            row["consumption_method"] = "observed_balance_delta"
        if previous and float(row["remaining"]) > float(previous.get("remaining") or 0):
            previous_reset = _parse_time(previous.get("reset_at"))
            current_time = _parse_time(timestamp) or _utc_now()
            expected = bool(previous_reset and current_time >= previous_reset)
            anomaly = {
                "kind": "expected_reset" if expected else "unexpected_credit_or_reset",
                "window_id": window_id,
                "account": account,
                "previous_remaining": previous.get("remaining"),
                "current_remaining": row["remaining"],
                "observed_at": timestamp,
                "evidence": evidence,
            }
            anomalies = list(bucket.get("anomalies") or [])
            anomalies.append(anomaly)
            bucket["anomalies"] = anomalies[-100:]
            row["anomaly"] = anomaly["kind"]
        self._write_state(state)
        return row

    def calibrate(
        self,
        providers: Iterable[str] | None = None,
        *,
        include_local_usage: bool = False,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Run cheap local probes only; authenticated balances remain explicit observations."""
        index = _provider_index(self.catalog)
        selected = list(providers or index)
        unknown = sorted(set(selected) - set(index))
        if unknown:
            raise ValueError(f"unknown providers: {unknown}")
        state = self._state()
        now = _utc_now()
        results: list[dict[str, Any]] = []
        for provider in selected:
            config = index[provider]
            bucket = state.setdefault("providers", {}).setdefault(provider, {})
            previous = bucket.get("calibration") or {}
            previous_at = _parse_time(previous.get("checked_at"))
            interval = float(config.get("calibration_interval_hours") or 24)
            if not force and previous_at and (now - previous_at).total_seconds() < interval * 3600:
                results.append({**previous, "provider": provider, "skipped": "fresh_cache"})
                continue
            executables = list(config.get("executables") or [])
            resolved = next((candidate for name in executables if (candidate := shutil.which(name))), None)
            if provider == "the_company-api":
                installed: bool | None = None
                available_hint = bool(os.environ.get("THE_COMPANY_API_KEY"))
            else:
                installed = bool(resolved)
                available_hint = installed
            calibration: dict[str, Any] = {
                "provider": provider,
                "checked_at": _iso_now(),
                "installed": installed,
                "resolved_executable": resolved,
                "credential_present": available_hint if provider == "the_company-api" else None,
                "method": "local_non_consuming_probe",
            }
            query = config.get("usage_query") or {}
            command = query.get("local_command")
            if include_local_usage and command and resolved:
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=20,
                        check=False,
                    )
                    output = (completed.stdout or completed.stderr or "")[:12000]
                    calibration["local_usage"] = {
                        "command": command,
                        "exit_code": completed.returncode,
                        "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                        "excerpt": output[:1500],
                    }
                except (OSError, subprocess.TimeoutExpired) as exc:
                    calibration["local_usage"] = {"command": command, "error": str(exc)}
            ledger_files = {
                "codex": ("sessions.jsonl", "provider-filtered session token ledger"),
                "claude": ("sessions.jsonl", "provider-filtered session token ledger"),
                "the_company-api": ("internal_by_caller_day.jsonl", "internal caller token ledger"),
            }
            if provider in ledger_files:
                ledger_name, ledger_scope = ledger_files[provider]
                ledger = omni_workspace_root() / "data" / "llm" / "token_ledger" / ledger_name
                calibration["derived_ledger"] = {
                    "path": str(ledger),
                    "scope": ledger_scope,
                    "available": ledger.is_file(),
                    "modified_at": (
                        datetime.fromtimestamp(ledger.stat().st_mtime, timezone.utc).isoformat()
                        if ledger.is_file()
                        else None
                    ),
                }
            bucket["calibration"] = calibration
            results.append(calibration)
        self._write_state(state)
        return results

    def status(self, providers: Iterable[str] | None = None) -> dict[str, Any]:
        """Read cached facts only. This method never probes a process, file tree or network."""
        index = _provider_index(self.catalog)
        selected = list(providers or index)
        state = self._state()
        now = _utc_now()
        rows: list[dict[str, Any]] = []
        for provider in selected:
            if provider not in index:
                raise ValueError(f"unknown provider: {provider}")
            config = index[provider]
            bucket = (state.get("providers") or {}).get(provider) or {}
            observations = list(bucket.get("observations") or [])
            latest: dict[tuple[str, str], dict[str, Any]] = {}
            for observation in observations:
                latest[(str(observation.get("account")), str(observation.get("window_id")))] = observation
            windows: list[dict[str, Any]] = []
            pressure = 0.0
            for observation in latest.values():
                item = dict(observation)
                observed = _parse_time(item.get("observed_at"))
                age_hours = None if not observed else (now - observed).total_seconds() / 3600
                item["age_hours"] = age_hours
                item["freshness"] = "unknown" if age_hours is None else ("fresh" if age_hours <= 26 else "stale")
                reset = _parse_time(item.get("reset_at"))
                reset_hours = None if not reset else (reset - now).total_seconds() / 3600
                item["reset_in_hours"] = reset_hours
                capacity = item.get("capacity")
                ratio = None if not capacity else float(item["remaining"]) / float(capacity)
                item["remaining_ratio"] = ratio
                expires_soon = bool(reset_hours is not None and 0 <= reset_hours <= float(self.catalog["policy"]["expiring_soon_hours"]))
                item["expires_soon"] = expires_soon
                if expires_soon and ratio is not None and ratio >= float(self.catalog["policy"]["high_remaining_ratio"]):
                    pressure = max(pressure, ratio * (1.0 - reset_hours / float(self.catalog["policy"]["expiring_soon_hours"])))
                windows.append(item)
            calibration = dict(bucket.get("calibration") or {})
            checked = _parse_time(calibration.get("checked_at"))
            calibration_age = None if not checked else (now - checked).total_seconds() / 3600
            rows.append(
                {
                    "id": provider,
                    "display_name": config.get("display_name"),
                    "runtime_provider": config.get("runtime_provider"),
                    "quota_model": config.get("quota_model"),
                    "reset_model": config.get("reset_model"),
                    "usage_query": config.get("usage_query"),
                    "calibration": calibration,
                    "calibration_freshness": (
                        "unknown" if calibration_age is None else ("fresh" if calibration_age <= float(config.get("calibration_interval_hours") or 24) else "stale")
                    ),
                    "windows": sorted(windows, key=lambda item: (str(item.get("account")), str(item.get("window_id")))),
                    "consumption_pressure": round(pressure, 4),
                    "recent_anomalies": list(bucket.get("anomalies") or [])[-10:],
                }
            )
        return {"schema": SCHEMA, "generated_at": _iso_now(), "network_probed": False, "providers": rows}

    def check_official_news(
        self,
        providers: Iterable[str] | None = None,
        *,
        force: bool = False,
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        """Conditionally fetch allowlisted official sources; never calls an LLM."""
        index = _provider_index(self.catalog)
        selected = list(providers or index)
        state = _read_json(self.news_path, {"schema": "omnicompany.official-provider-news.v1", "sources": {}})
        source_state = state.setdefault("sources", {})
        results: list[dict[str, Any]] = []
        now = _utc_now()
        interval = float(self.catalog["policy"].get("default_news_interval_hours") or 168)
        for provider in selected:
            if provider not in index:
                raise ValueError(f"unknown provider: {provider}")
            for url in index[provider].get("news_sources") or []:
                prior = dict(source_state.get(url) or {})
                checked = _parse_time(prior.get("checked_at"))
                if not force and checked and (now - checked).total_seconds() < interval * 3600:
                    results.append({"provider": provider, "url": url, "status": "skipped_fresh_cache", "changed": False})
                    continue
                headers = {"User-Agent": "OmniCompany-AgentResourceControl/1.0"}
                if prior.get("etag"):
                    headers["If-None-Match"] = str(prior["etag"])
                if prior.get("last_modified"):
                    headers["If-Modified-Since"] = str(prior["last_modified"])
                req = urllib.request.Request(url, headers=headers, method="GET")
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - catalog allowlist
                        raw = response.read(2_000_000)
                        digest = hashlib.sha256(raw).hexdigest()
                        text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
                        title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else ""
                        changed = bool(prior.get("sha256") and prior.get("sha256") != digest)
                        source_state[url] = {
                            "provider": provider,
                            "checked_at": _iso_now(),
                            "etag": response.headers.get("ETag"),
                            "last_modified": response.headers.get("Last-Modified"),
                            "sha256": digest,
                            "title": title,
                            "changed_at": _iso_now() if changed else prior.get("changed_at"),
                        }
                        results.append({"provider": provider, "url": url, "status": "fetched", "changed": changed, "title": title})
                except urllib.error.HTTPError as exc:
                    if exc.code == 304:
                        prior["checked_at"] = _iso_now()
                        source_state[url] = prior
                        results.append({"provider": provider, "url": url, "status": "not_modified", "changed": False})
                    else:
                        results.append({"provider": provider, "url": url, "status": "error", "error": f"HTTP {exc.code}"})
                except OSError as exc:
                    results.append({"provider": provider, "url": url, "status": "error", "error": str(exc)})
        state["updated_at"] = _iso_now()
        _atomic_json(self.news_path, state)
        return {"checked_at": state["updated_at"], "results": results, "changed": [row for row in results if row.get("changed")]}

    def record_review(
        self,
        *,
        reviewer_session: str,
        reviewer_model: str,
        subject_session: str,
        subject_model: str,
        severity: str,
        task_type: str,
        issues: list[str] | None = None,
        upstream_factors: list[str] | None = None,
        evidence: list[str] | None = None,
        reviewer_provider: str = "",
        subject_provider: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(VALID_SEVERITIES)}")
        issues = [item.strip() for item in (issues or []) if item.strip()]
        upstream_factors = [item.strip() for item in (upstream_factors or []) if item.strip()]
        evidence = [item.strip() for item in (evidence or []) if item.strip()]
        if severity != "pass" and not issues:
            raise ValueError("imperfect/severe reviews require at least one issue")
        if severity != "pass" and not upstream_factors:
            raise ValueError("imperfect/severe reviews require upstream_factors; use 'none_observed' when appropriate")
        if not evidence:
            raise ValueError("explicit peer reviews require at least one evidence reference")
        row = {
            "schema": REVIEW_SCHEMA,
            "id": str(uuid.uuid4()),
            "recorded_at": _iso_now(),
            "reviewer_session": reviewer_session,
            "reviewer_model": reviewer_model,
            "reviewer_provider": reviewer_provider,
            "subject_session": subject_session,
            "subject_model": subject_model,
            "subject_provider": subject_provider,
            "same_model_different_session": reviewer_model == subject_model and reviewer_session != subject_session,
            "severity": severity,
            "task_type": task_type or "general",
            "issues": issues,
            "upstream_factors": upstream_factors,
            "evidence": evidence,
            "note": note,
        }
        row["fingerprint"] = peer_review_fingerprint(row)
        existing, _ = deduplicate_peer_reviews(self.reviews(raw=True))
        for prior in existing:
            if prior.get("fingerprint") == row["fingerprint"]:
                result = dict(prior)
                result["deduplicated"] = True
                return result
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        with self.review_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def record_review_amendment(
        self,
        *,
        actor_id: str,
        superseded_review_ids: list[str],
        replacement_review_id: str,
        reason: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        """Append a correction link without impersonating or deleting a reviewer row."""
        actor_id = actor_id.strip()
        superseded_review_ids = list(dict.fromkeys(
            item.strip() for item in superseded_review_ids if item.strip()
        ))
        replacement_review_id = replacement_review_id.strip()
        reason = reason.strip()
        evidence = [item.strip() for item in evidence if item.strip()]
        if not actor_id:
            raise ValueError("actor_id is required")
        if not superseded_review_ids:
            raise ValueError("at least one superseded_review_id is required")
        if not replacement_review_id:
            raise ValueError("replacement_review_id is required")
        if replacement_review_id in superseded_review_ids:
            raise ValueError("replacement review cannot supersede itself")
        if not reason:
            raise ValueError("reason is required")
        if not evidence:
            raise ValueError("at least one evidence reference is required")

        review_by_id = {
            str(row.get("id") or ""): row
            for row in deduplicate_peer_reviews(self.reviews(raw=True))[0]
            if row.get("id")
        }
        missing = sorted(
            review_id
            for review_id in [*superseded_review_ids, replacement_review_id]
            if review_id not in review_by_id
        )
        if missing:
            raise ValueError(f"unknown peer review ids: {missing}")
        replacement = review_by_id[replacement_review_id]
        cohort = (
            str(replacement.get("subject_session") or ""),
            str(replacement.get("subject_model") or ""),
            str(replacement.get("task_type") or "general"),
        )
        for review_id in superseded_review_ids:
            prior = review_by_id[review_id]
            prior_cohort = (
                str(prior.get("subject_session") or ""),
                str(prior.get("subject_model") or ""),
                str(prior.get("task_type") or "general"),
            )
            if prior_cohort != cohort:
                raise ValueError("review amendment cannot cross subject session/model/task cohort")

        row = {
            "schema": REVIEW_AMENDMENT_SCHEMA,
            "id": str(uuid.uuid4()),
            "recorded_at": _iso_now(),
            "actor_id": actor_id,
            "superseded_review_ids": superseded_review_ids,
            "replacement_review_id": replacement_review_id,
            "reason": reason,
            "evidence": evidence,
        }
        row["fingerprint"] = peer_review_amendment_fingerprint(row)
        for prior in self.review_amendments():
            if prior.get("fingerprint") == row["fingerprint"]:
                result = dict(prior)
                result["deduplicated"] = True
                return result
        self.review_amendment_path.parent.mkdir(parents=True, exist_ok=True)
        with self.review_amendment_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def review_amendments(self) -> list[dict[str, Any]]:
        if not self.review_amendment_path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.review_amendment_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema") == REVIEW_AMENDMENT_SCHEMA:
                rows.append(row)
        return rows

    def reviews(self, *, raw: bool = False) -> list[dict[str, Any]]:
        if not self.review_path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.review_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema") == REVIEW_SCHEMA:
                rows.append(row)
        if raw:
            return rows
        return resolve_peer_reviews(rows, self.review_amendments())[0]

    def review_matrix(self, *, task_type: str | None = None) -> dict[str, Any]:
        raw_rows = self.reviews(raw=True)
        review_rows, duplicate_rows_ignored, superseded_rows_ignored = resolve_peer_reviews(
            raw_rows, self.review_amendments()
        )
        pairs: dict[tuple[str, str], dict[str, Any]] = {}
        subject_scores: dict[tuple[str, str], dict[str, Any]] = {}
        for row in review_rows:
            if task_type and row.get("task_type") != task_type:
                continue
            pair_key = (str(row.get("reviewer_model")), str(row.get("subject_model")))
            pair = pairs.setdefault(pair_key, {"reviewer_model": pair_key[0], "subject_model": pair_key[1], "pass": 0, "imperfect": 0, "severe": 0, "total": 0, "same_model_cross_session": 0})
            severity = str(row.get("severity"))
            pair[severity] = int(pair.get(severity) or 0) + 1
            pair["total"] += 1
            if row.get("same_model_different_session"):
                pair["same_model_cross_session"] += 1
            subject_key = (str(row.get("subject_model")), str(row.get("task_type") or "general"))
            subject = subject_scores.setdefault(subject_key, {"subject_model": subject_key[0], "task_type": subject_key[1], "pass": 0, "imperfect": 0, "severe": 0, "total": 0})
            subject[severity] = int(subject.get(severity) or 0) + 1
            subject["total"] += 1
        for item in list(pairs.values()) + list(subject_scores.values()):
            total = max(1, int(item["total"]))
            item["defect_rate"] = round((int(item["imperfect"]) + 2 * int(item["severe"])) / (2 * total), 4)
        return {
            "schema": "omnicompany.agent-peer-review-matrix.v1",
            "generated_at": _iso_now(),
            "default_pass_is_implicit": True,
            "raw_total": len(raw_rows),
            "unique_total": len(review_rows),
            "duplicate_rows_ignored": duplicate_rows_ignored,
            "superseded_rows_ignored": superseded_rows_ignored,
            "amendment_total": len(self.review_amendments()),
            "task_type": task_type,
            "pairs": sorted(pairs.values(), key=lambda item: (item["reviewer_model"], item["subject_model"])),
            "subjects": sorted(subject_scores.values(), key=lambda item: (item["subject_model"], item["task_type"])),
        }

    def user_feedback_matrix(self, *, task_type: str | None = None) -> dict[str, Any]:
        """Aggregate explicit, attributed user feedback by model and task.

        Facility-scoped and unattributed feedback remains visible in the HR ledger but
        cannot penalise an arbitrary model.  Only high-confidence rows participate in
        routing feedback.
        """
        from omnicompany.packages.services._core.hr.note import read_notes

        subjects: dict[tuple[str, str], dict[str, Any]] = {}
        canonical_models: dict[str, str] = {}
        for model_row in self.catalog["models"]:
            model_id = str(model_row["id"])
            canonical_models[model_id.lower()] = model_id
            for alias in model_row.get("aliases") or []:
                canonical_models[str(alias).lower()] = model_id
        ignored_unattributed = 0
        ignored_low_confidence = 0
        for row in read_notes(data_dir=self.hr_data_dir):
            if row.source != "user":
                continue
            if row.extraction_confidence != "high":
                ignored_low_confidence += 1
                continue
            if not row.actor_id:
                ignored_unattributed += 1
                continue
            parts = row.actor_id.split(":", 2)
            if len(parts) != 3 or not parts[1]:
                ignored_unattributed += 1
                continue
            normalized_task = str(row.task_type or "general")
            if task_type and normalized_task != task_type:
                continue
            canonical_model = canonical_models.get(parts[1].lower(), parts[1])
            key = (canonical_model, normalized_task)
            subject = subjects.setdefault(key, {
                "subject_model": canonical_model, "task_type": normalized_task,
                "pass": 0, "imperfect": 0, "severe": 0, "total": 0,
                "note_ids": [], "source_refs": [],
            })
            subject[row.severity] += 1
            subject["total"] += 1
            subject["note_ids"].append(row.id)
            subject["source_refs"].append(row.source_ref)
        for item in subjects.values():
            total = max(1, int(item["total"]))
            item["defect_rate"] = round(
                (int(item["imperfect"]) + 2 * int(item["severe"])) / (2 * total), 4
            )
        return {
            "schema": "omnicompany.agent-user-feedback-matrix.v1",
            "generated_at": _iso_now(),
            "task_type": task_type,
            "evidence_role": "primary",
            "ignored_unattributed": ignored_unattributed,
            "ignored_low_confidence": ignored_low_confidence,
            "subjects": sorted(subjects.values(), key=lambda item: (item["subject_model"], item["task_type"])),
        }

    def models(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.catalog["models"]]

    def recommend(
        self,
        task: str,
        *,
        task_type: str = "auto",
        require_multimodal: bool = False,
        provider: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        normalized_type = self._infer_task_type(task, task_type, require_multimodal)
        resource_rows = {row["id"]: row for row in self.status()["providers"]}
        review_rows = {
            (row["subject_model"], row["task_type"]): row
            for row in self.review_matrix()["subjects"]
        }
        user_feedback_rows = {
            (row["subject_model"], row["task_type"]): row
            for row in self.user_feedback_matrix()["subjects"]
        }
        candidates: list[dict[str, Any]] = []
        minimum_review_samples = int(self.catalog["policy"].get("minimum_review_samples") or 3)
        minimum_user_feedback_samples = int(
            self.catalog["policy"].get("minimum_user_feedback_samples") or 1
        )
        for model in self.catalog["models"]:
            modalities = set(model.get("modalities") or ["text"])
            if require_multimodal and "image" not in modalities and "multimodal" not in set(model.get("capabilities") or []):
                continue
            model_providers = list(model.get("providers") or [])
            if provider:
                if provider not in model_providers:
                    continue
                model_providers = [provider]
            selected_provider, provider_score, provider_reasons = self._best_provider(model_providers, resource_rows)
            if not selected_provider:
                continue
            score, reasons = self._model_score(model, normalized_type)
            score += provider_score
            reasons.extend(provider_reasons)
            review = review_rows.get((model["id"], normalized_type))
            user_feedback = user_feedback_rows.get((model["id"], normalized_type))
            user_samples = int(user_feedback.get("total") or 0) if user_feedback else 0
            user_defect_rate = float(user_feedback.get("defect_rate") or 0) if user_feedback else 0.0
            user_applied = user_samples >= minimum_user_feedback_samples
            user_penalty = user_defect_rate * 20 if user_applied else 0.0
            matched_samples = int(review.get("total") or 0) if review else 0
            defect_rate = float(review.get("defect_rate") or 0) if review else 0.0
            review_applied = not user_applied and matched_samples >= minimum_review_samples
            penalty = defect_rate * 20 if review_applied else 0.0
            if user_applied:
                score -= user_penalty
                reasons.append(
                    f"user feedback adjustment -{user_penalty:.1f} ({user_samples} matched samples)"
                )
            if review_applied:
                score -= penalty
                reasons.append(f"peer review penalty -{penalty:.1f} ({matched_samples} matched samples)")
            candidates.append(
                {
                    "model": model["id"],
                    "display_name": model.get("display_name"),
                    "tier": model["tier"],
                    "provider": selected_provider,
                    "runtime_provider": _provider_index(self.catalog)[selected_provider].get("runtime_provider"),
                    "score": round(score, 3),
                    "reasons": reasons,
                    "user_feedback_adjustment": {
                        "role": "primary",
                        "task_type": normalized_type,
                        "matched_samples": user_samples,
                        "minimum_samples": minimum_user_feedback_samples,
                        "defect_rate": round(user_defect_rate, 4) if user_feedback else None,
                        "applied": user_applied,
                        "penalty": round(user_penalty, 3),
                        "reason": (
                            "explicit attributed user feedback takes precedence"
                            if user_applied else "not applied: no high-confidence same-task user feedback"
                        ),
                    },
                    "peer_review_adjustment": {
                        "role": "auxiliary",
                        "task_type": normalized_type,
                        "matched_samples": matched_samples,
                        "minimum_samples": minimum_review_samples,
                        "defect_rate": round(defect_rate, 4) if review else None,
                        "applied": review_applied,
                        "penalty": round(penalty, 3),
                        "reason": (
                            "minimum matched-sample gate reached"
                            if review_applied
                            else (
                                "not applied: explicit user feedback takes precedence"
                                if user_applied else "not applied: insufficient same-task samples"
                            )
                        ),
                    },
                }
            )
        candidates.sort(key=lambda item: (-item["score"], item["tier"], item["model"]))
        return {
            "task_type": normalized_type,
            "task": task,
            "recommendations": candidates[: max(1, limit)],
            "facts": {
                "tier_authority": "user_pinned_2026-08-10",
                "budget_source": "cached_provider_observations",
                "network_probed": False,
                "implicit_pass_reviews_not_counted": True,
                "minimum_review_samples": minimum_review_samples,
                "minimum_user_feedback_samples": minimum_user_feedback_samples,
                "evaluation_precedence": "user_feedback_primary_peer_review_auxiliary",
            },
        }

    @staticmethod
    def _infer_task_type(task: str, requested: str, require_multimodal: bool) -> str:
        if require_multimodal:
            return "multimodal"
        if requested and requested != "auto":
            return TASK_ALIASES.get(requested, requested)
        lowered = task.lower()
        patterns = [
            ("multimodal", ("image", "screenshot", "视觉", "图片", "截图", "多模态")),
            ("frontend", ("frontend", "ui", "ux", "网页", "前端", "动效", "css")),
            ("chinese_writing", ("中文", "文案", "成文", "写作", "润色")),
            ("backend", ("backend", "后端", "数据库", "api", "性能", "安全")),
            ("planning", ("plan", "架构", "设计", "计划")),
            ("batch", ("batch", "批量", "大批")),
            ("long_running", ("long-running", "long running", "长跑", "长期运行")),
            ("testing", ("test", "测试", "canary", "回归")),
            ("simple", ("simple", "简单", "机械", "低智能")),
        ]
        return next((name for name, needles in patterns if any(needle in lowered for needle in needles)), "general")

    @staticmethod
    def _model_score(model: dict[str, Any], task_type: str) -> tuple[float, list[str]]:
        capabilities = set(model.get("capabilities") or [])
        tier = str(model.get("tier"))
        score = 10.0
        reasons = [f"user-pinned {tier}"]
        if task_type in capabilities:
            score += 30
            reasons.append(f"direct capability match: {task_type}")
        elif "general" in capabilities:
            score += 8
            reasons.append("general capability fallback")
        if task_type in {"simple", "batch", "long_running", "testing", "general"} and tier == "T4":
            score += 18
            reasons.append("T4 value-first policy for simple/batch/long-run/default work")
        if task_type in {"frontend", "presentation", "expressive_content"} and tier == "T1":
            score += 15
            reasons.append("T1 authority for frontend/presentation work")
        if task_type == "chinese_writing" and model.get("id") == "qwen3.8-max":
            score += 20
            reasons.append("Qwen Chinese-writing preference")
        if task_type == "backend" and model.get("id") in {"gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"}:
            score += 20
            reasons.append("Sol backend/safety/performance/faithfulness preference")
        if task_type == "multimodal" and model.get("id") == "gpt-5.6-luna":
            score += 20
            reasons.append("Luna multimodal preference")
        return score, reasons

    @staticmethod
    def _best_provider(
        providers: list[str],
        resources: dict[str, dict[str, Any]],
    ) -> tuple[str | None, float, list[str]]:
        ranked: list[tuple[float, str, list[str]]] = []
        for provider in providers:
            row = resources.get(provider) or {}
            calibration = row.get("calibration") or {}
            installed = calibration.get("installed")
            runtime_provider = row.get("runtime_provider")
            if runtime_provider and installed is False:
                continue
            score = 0.0
            reasons: list[str] = []
            if installed is True:
                score += 8
                reasons.append(f"{provider} CLI observed installed")
            elif runtime_provider is None:
                score += 1
                reasons.append(f"{provider} API availability not inferred from CLI")
            else:
                reasons.append(f"{provider} availability unknown")
            pressure = float(row.get("consumption_pressure") or 0)
            if pressure > 0:
                boost = pressure * 40
                score += boost
                reasons.append(f"expiring-quota pressure +{boost:.1f}")
            ranked.append((score, provider, reasons))
        if not ranked:
            return None, 0.0, []
        ranked.sort(key=lambda item: (-item[0], item[1]))
        score, provider, reasons = ranked[0]
        return provider, score, reasons


__all__ = [
    "REVIEW_AMENDMENT_SCHEMA",
    "REVIEW_SCHEMA",
    "ResourceControl",
    "default_catalog_path",
    "default_state_dir",
    "peer_review_amendment_fingerprint",
    "resolve_peer_reviews",
    "load_catalog",
]
