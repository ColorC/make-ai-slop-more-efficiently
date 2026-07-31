from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .store import ObservatoryStore


@dataclass(frozen=True)
class PublicSiteBudget:
    local_p95_ms: float = 500.0
    browser_p95_ms: float = 1500.0
    home_bytes: int = 100_000
    javascript_bytes: int = 200_000
    stylesheet_bytes: int = 200_000
    catalog_bytes: int = 750_000
    semantic_report_bytes: int = 750_000
    concurrent_requests: int = 20
    concurrent_wall_ms: float = 2500.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


class BrowserEvidenceCollector:
    """Capture current browser quality evidence for every published design spec."""

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    @staticmethod
    def _snapshot(page: Any) -> dict[str, Any]:
        return page.evaluate(
            r"""() => {
              const label = element => {
                const labelledBy = (element.getAttribute('aria-labelledby') || '')
                  .split(/\s+/)
                  .filter(Boolean)
                  .map(id => document.getElementById(id)?.textContent || '')
                  .join(' ')
                const nativeLabels = element.labels
                  ? [...element.labels].map(item => item.textContent || '').join(' ')
                  : ''
                return (
                  element.getAttribute('aria-label') || labelledBy || nativeLabels ||
                  element.getAttribute('title') || element.getAttribute('alt') ||
                  element.textContent || element.value || ''
                ).trim()
              }
              const controls = [...document.querySelectorAll(
                'a[href],button,input,select,textarea,[role="button"],[role="link"]'
              )]
              const controlsWithoutName = controls
                .filter(element => !label(element))
                .map(element => element.outerHTML.slice(0, 180))
              const ids = [...document.querySelectorAll('[id]')].map(element => element.id)
              const duplicateIds = [...new Set(ids.filter(
                (id, index) => ids.indexOf(id) !== index
              ))]
              const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
                .map(element => ({
                  level: Number(element.tagName.slice(1)),
                  text: (element.textContent || '').trim(),
                }))
              let previous = 0
              const headingOrderValid = headings.every(item => {
                const valid = previous === 0 || item.level <= previous + 1
                previous = item.level
                return valid
              })
              const skip = document.querySelector('a[href^="#"]')
              const skipTarget = skip?.getAttribute('href')
              const overflow = [...document.querySelectorAll('body *')]
                .filter(element => element.getBoundingClientRect().right > innerWidth + 1)
                .map(element => ({
                  tag: element.tagName,
                  className: String(element.className || ''),
                  right: Math.round(element.getBoundingClientRect().right),
                }))
                .slice(0, 12)
              return {
                url: location.href,
                title: document.title,
                lang: document.documentElement.lang,
                controls_without_name: controlsWithoutName,
                images_without_alt: [...document.images]
                  .filter(image => !image.hasAttribute('alt'))
                  .map(image => image.src),
                broken_images: [...document.images]
                  .filter(image => image.complete && image.naturalWidth === 0)
                  .map(image => image.src),
                duplicate_ids: duplicateIds,
                positive_tabindex: [...document.querySelectorAll('[tabindex]')]
                  .filter(element => Number(element.getAttribute('tabindex')) > 0)
                  .map(element => element.outerHTML.slice(0, 180)),
                heading_order_valid: headingOrderValid,
                headings,
                skip_target: Boolean(
                  skipTarget && skipTarget.length > 1 && document.querySelector(skipTarget)
                ),
                landmarks: {
                  main: document.querySelectorAll('main').length,
                  nav: document.querySelectorAll('nav').length,
                  header: document.querySelectorAll('header').length,
                  footer: document.querySelectorAll('footer').length,
                },
                horizontal_overflow: overflow,
                has_surfaces: Boolean(document.querySelector('#spec-surfaces')),
                has_journey: Boolean(document.querySelector(
                  '#spec-core-loop, #spec-interaction, .journey'
                )),
                has_mechanisms: Boolean(document.querySelector(
                  '#spec-rules .mechanism, .mechanism'
                )),
                has_resources: Boolean(document.querySelector(
                  '#spec-rules table, .resources'
                )),
                has_sources: Boolean(document.querySelector('#spec-sources, .source-list')),
              }
            }"""
        )

    @staticmethod
    def _verify_surface_modes(page: Any) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        cards = page.locator("[data-surface-card]")
        for card_index in range(cards.count()):
            card = cards.nth(card_index)
            card.scroll_into_view_if_needed()
            for mode in ("evidence", "reconstruction", "layout"):
                try:
                    card.locator(f'[data-surface-mode="{mode}"]').click()
                    panel = card.locator(f'[data-surface-panel="{mode}"]')
                    if not panel.is_visible():
                        raise AssertionError("panel is hidden after activation")
                    image = panel.locator("img")
                    if image.count() and not image.first.evaluate(
                        "image => image.decode().then(() => image.naturalWidth > 0)"
                    ):
                        raise AssertionError("visible image did not decode")
                    if mode == "layout" and card.locator(".layout-box").count() < 1:
                        raise AssertionError("layout has no machine-readable boxes")
                except Exception as exc:  # noqa: BLE001 - evidence records browser failures
                    failures.append(
                        {
                            "surface_index": card_index,
                            "mode": mode,
                            "error": str(exc),
                        }
                    )
        return failures

    def capture(
        self,
        base_url: str,
        *,
        samples: int = 5,
        write: bool = True,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment contract
            raise RuntimeError("Playwright is required to capture browser evidence") from exc

        home_url = urljoin(base_url.rstrip("/") + "/", "game-observatory/")
        navigation_samples: list[float] = []
        console_errors: list[str] = []
        failed_requests: list[dict[str, Any]] = []
        http_errors: list[dict[str, Any]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(
                    {"url": request.url, "failure": request.failure}
                ),
            )
            page.on(
                "response",
                lambda response: (
                    http_errors.append({"url": response.url, "status": response.status})
                    if response.status >= 400
                    else None
                ),
            )
            for _ in range(samples):
                started = time.perf_counter()
                response = page.goto(home_url, wait_until="networkidle")
                if response is None or response.status != 200:
                    raise RuntimeError("Game Observatory home did not return HTTP 200")
                navigation_samples.append(
                    round((time.perf_counter() - started) * 1000, 3)
                )
            home = self._snapshot(page)
            page.set_viewport_size({"width": 390, "height": 844})
            home["mobile_horizontal_overflow"] = self._snapshot(page)[
                "horizontal_overflow"
            ]
            page.set_viewport_size({"width": 1440, "height": 1100})

            reports: dict[str, dict[str, Any]] = {}
            for report in self.store.list_reports():
                report_url = urljoin(
                    base_url.rstrip("/") + "/",
                    f"game-observatory/report/{report.slug}",
                )
                response = page.goto(report_url, wait_until="networkidle")
                if response is None or response.status != 200:
                    raise RuntimeError(f"Design spec did not return HTTP 200: {report.slug}")
                page.locator(".design-reader").wait_for()
                surface_failures = self._verify_surface_modes(page)
                view = self._snapshot(page)
                view["surface_view_failures"] = surface_failures
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#spec-sources").scroll_into_view_if_needed()
                view["mobile_horizontal_overflow"] = self._snapshot(page)[
                    "horizontal_overflow"
                ]
                page.set_viewport_size({"width": 1440, "height": 1100})
                reports[report.slug] = view
            browser.close()

        payload = {
            "schema": "game-observatory.browser-quality-evidence.v2",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "base_url": base_url,
            "navigation_samples_ms": navigation_samples,
            "home": home,
            "reports": reports,
            "report": next(iter(reports.values()), {}),
            "console_errors": console_errors,
            "failed_requests": failed_requests,
            "http_errors": http_errors,
        }
        if write:
            output = self.store.export_root / "public-site-browser-evidence.json"
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            payload["path"] = str(output)
        return payload


class PublicSiteQualityVerifier:
    """Repeatable HTTP/content gate, augmented by a real browser evidence file."""

    def __init__(self, store: ObservatoryStore, budget: PublicSiteBudget | None = None) -> None:
        self.store = store
        self.budget = budget or PublicSiteBudget()

    @staticmethod
    def _fetch(base_url: str, path: str) -> dict[str, Any]:
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        request = Request(url, headers={"User-Agent": "game-observatory-quality/1"})
        started = time.perf_counter()
        with urlopen(request, timeout=10) as response:  # noqa: S310 - caller supplies validation URL
            body = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
            status = int(response.status)
        return {
            "url": url,
            "status": status,
            "bytes": len(body),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "headers": headers,
            "body": body,
        }

    @staticmethod
    def _browser_gate(payload: dict[str, Any] | None) -> tuple[bool, list[dict[str, Any]]]:
        if not payload:
            return False, [{"id": "browser-evidence-present", "passed": False}]
        checks: list[dict[str, Any]] = []
        report_views = payload.get("reports") or {"report": payload.get("report") or {}}
        views = {"home": payload.get("home") or {}, **report_views}
        for view_name, view in views.items():
            checks.extend(
                [
                    {"id": f"{view_name}-controls-have-names", "passed": not view.get("controls_without_name")},
                    {"id": f"{view_name}-images-have-alt", "passed": not view.get("images_without_alt")},
                    {"id": f"{view_name}-images-decode", "passed": not view.get("broken_images")},
                    {"id": f"{view_name}-ids-unique", "passed": not view.get("duplicate_ids")},
                    {"id": f"{view_name}-tab-order-natural", "passed": not view.get("positive_tabindex")},
                    {"id": f"{view_name}-heading-order", "passed": bool(view.get("heading_order_valid"))},
                    {"id": f"{view_name}-desktop-no-overflow", "passed": not view.get("horizontal_overflow")},
                    {"id": f"{view_name}-mobile-no-overflow", "passed": not view.get("mobile_horizontal_overflow")},
                    {"id": f"{view_name}-surface-views", "passed": not view.get("surface_view_failures")},
                ]
            )
        home = payload.get("home") or {}
        checks.extend(
            [
                {"id": "document-language", "passed": home.get("lang") == "zh-CN"},
                {"id": "skip-link-target", "passed": bool(home.get("skip_target"))},
                {"id": "semantic-landmarks", "passed": all((home.get("landmarks") or {}).get(key) for key in ("main", "nav", "header", "footer"))},
                {"id": "console-errors", "passed": not payload.get("console_errors")},
                {"id": "failed-requests", "passed": not payload.get("failed_requests")},
                {"id": "http-errors", "passed": not payload.get("http_errors")},
            ]
        )
        for slug, report in report_views.items():
            checks.append(
                {
                    "id": f"{slug}-dynamic-report-sections",
                    "passed": all(
                        report.get(key)
                        for key in (
                            "has_surfaces",
                            "has_journey",
                            "has_mechanisms",
                            "has_resources",
                            "has_sources",
                        )
                    ),
                }
            )
        return all(item["passed"] for item in checks), checks

    def verify(
        self,
        base_url: str,
        *,
        browser_evidence_path: Path | None = None,
        samples: int = 5,
        write: bool = True,
    ) -> dict[str, Any]:
        browser_evidence: dict[str, Any] | None = None
        if browser_evidence_path and browser_evidence_path.is_file():
            browser_evidence = json.loads(browser_evidence_path.read_text(encoding="utf-8"))

        paths = {
            "home": "/game-observatory/",
            "javascript": "/game-observatory/app.js",
            "stylesheet": "/game-observatory/styles.css",
            "catalog": "/api/game-observatory/catalog",
        }
        first = {name: self._fetch(base_url, path) for name, path in paths.items()}
        latency_samples = [self._fetch(base_url, paths["home"])["elapsed_ms"] for _ in range(samples)]
        latency_samples += [
            self._fetch(base_url, paths["catalog"])["elapsed_ms"] for _ in range(samples)
        ]
        catalog = json.loads(first["catalog"]["body"].decode("utf-8"))
        reports = catalog.get("reports") or []
        semantic = [
            self._fetch(base_url, f"/game-observatory/reports/{item['slug']}") for item in reports
        ]

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.budget.concurrent_requests) as pool:
            concurrent = list(
                pool.map(
                    lambda _: self._fetch(base_url, paths["catalog"]),
                    range(self.budget.concurrent_requests),
                )
            )
        concurrent_wall_ms = round((time.perf_counter() - started) * 1000, 3)

        home_text = first["home"]["body"].decode("utf-8", errors="replace")
        css_text = first["stylesheet"]["body"].decode("utf-8", errors="replace")
        response_checks = [
            {"id": f"{name}-http-200", "passed": item["status"] == 200}
            for name, item in first.items()
        ]
        response_checks.extend(
            [
                {"id": "content-security-policy", "passed": bool(first["home"]["headers"].get("content-security-policy"))},
                {"id": "catalog-etag", "passed": bool(first["catalog"]["headers"].get("etag"))},
                {"id": "versioned-javascript", "passed": "/game-observatory/app.js?v=" in home_text},
                {"id": "versioned-stylesheet", "passed": "/game-observatory/styles.css?v=" in home_text},
            ]
        )
        performance_checks = [
            {"id": "local-p95", "passed": _percentile(latency_samples, 0.95) <= self.budget.local_p95_ms},
            {"id": "home-size", "passed": first["home"]["bytes"] <= self.budget.home_bytes},
            {"id": "javascript-size", "passed": first["javascript"]["bytes"] <= self.budget.javascript_bytes},
            {"id": "stylesheet-size", "passed": first["stylesheet"]["bytes"] <= self.budget.stylesheet_bytes},
            {"id": "catalog-size", "passed": first["catalog"]["bytes"] <= self.budget.catalog_bytes},
            {"id": "semantic-report-size", "passed": all(item["bytes"] <= self.budget.semantic_report_bytes for item in semantic)},
            {"id": "concurrent-requests", "passed": all(item["status"] == 200 for item in concurrent)},
            {"id": "concurrent-wall", "passed": concurrent_wall_ms <= self.budget.concurrent_wall_ms},
        ]
        browser_p95 = _percentile(
            [float(item) for item in (browser_evidence or {}).get("navigation_samples_ms", [])],
            0.95,
        )
        performance_checks.append(
            {"id": "browser-navigation-p95", "passed": browser_p95 <= self.budget.browser_p95_ms}
        )

        responsive_checks = [
            {"id": "narrow-layout-breakpoint", "passed": "@media (max-width: 680px)" in css_text},
            {"id": "tablet-layout-breakpoint", "passed": "@media (max-width: 980px)" in css_text},
            {
                "id": "single-column-report",
                "passed": bool(
                    re.search(
                        r"[^{}]*(?:\.report-body|\.design-reader)[^{}]*"
                        r"\{[^{}]*grid-template-columns:\s*1fr\s*;",
                        css_text,
                    )
                ),
            },
            {
                "id": "single-column-surfaces",
                "passed": bool(
                    re.search(
                        r"[^{}]*\.surface-card[^{}]*"
                        r"\{[^{}]*grid-template-columns:\s*1fr\s*;",
                        css_text,
                    )
                ),
            },
            {"id": "reduced-motion", "passed": "prefers-reduced-motion: reduce" in css_text},
            {"id": "keyboard-focus", "passed": ":focus-visible" in css_text},
        ]
        browser_ok, browser_checks = self._browser_gate(browser_evidence)

        archive_results = []
        for report in reports:
            active_sources = [item for item in report.get("sources", []) if item.get("status") == "active"]
            surfaces = report.get("surfaces") or []
            task = report.get("benchmark_task")
            benchmark_complete = not task or bool(report.get("runs") and report.get("artifacts"))
            design_spec = report.get("design_spec") or {}
            artifacts = {item.get("id"): item for item in report.get("artifacts") or []}
            visual_ids = {
                artifact_id
                for artifact_id, item in artifacts.items()
                if item.get("kind") in {"screenshot", "video_frame"}
            }
            design_artifacts = design_spec.get("design_artifacts") or []
            reviewed_design_artifacts = [
                item for item in design_artifacts if item.get("review_status") == "reviewed"
            ]
            layout_surface_ids = {
                item.get("surface_id") for item in design_spec.get("layout_specs") or []
            }
            required_surface_ids = {
                item.get("id") for item in surfaces if item.get("publication_required", True)
            }
            coverage = {
                item.get("section"): item for item in design_spec.get("section_coverage") or []
            }
            always_required = {
                "scope",
                "system_overview",
                "player_goals",
                "entry_unlock",
                "core_loop",
                "information_architecture",
                "surface_design",
                "interaction_flow",
                "state_matrix",
                "rules_mechanics",
                "feedback",
                "failure_recovery",
                "dependencies",
                "player_voice",
                "version_provenance",
            }
            checks = {
                "v03_contract": report.get("contract_version") == "reverse-engineered-game-design-spec.v0.3",
                "design_spec": bool(design_spec),
                "flow": bool(report.get("flow")),
                "mechanisms": bool(report.get("mechanisms")),
                "sources": bool(active_sources),
                "surfaces": bool(surfaces) and all(item.get("elements") for item in surfaces),
                "surface_visual_evidence": all(
                    set(item.get("artifact_ids") or []) & visual_ids
                    for item in surfaces
                    if item.get("publication_required", True)
                ),
                "surface_reverse_design": all(
                    any(
                        surface_id in (item.get("surface_ids") or [])
                        and item.get("kind") in {"annotated_plate", "layout_spec", "wireframe"}
                        for item in reviewed_design_artifacts
                    )
                    for surface_id in required_surface_ids
                ),
                "machine_readable_layouts": required_surface_ids <= layout_surface_ids,
                "interaction_diagram": any(
                    item.get("kind") in {"wireflow", "interaction_diagram"}
                    and item.get("flow_node_ids")
                    for item in reviewed_design_artifacts
                ),
                "interaction_specs": bool(design_spec.get("interaction_specs")),
                "state_matrices": bool(design_spec.get("state_matrices")),
                "feedback_specs": bool(design_spec.get("feedback_specs")),
                "failure_recovery_specs": bool(design_spec.get("failure_recovery_specs")),
                "dependency_specs": bool(design_spec.get("dependency_specs")),
                "player_voice_bindings": bool(design_spec.get("player_voice_ids"))
                and all(
                    item.get("target_object_ids") or item.get("system_node_id")
                    for item in report.get("player_voices") or []
                    if item.get("id") in set(design_spec.get("player_voice_ids") or [])
                ),
                "section_coverage": all(
                    coverage.get(section, {}).get("status") == "complete"
                    for section in always_required
                )
                and all(
                    coverage.get(section, {}).get("status") in {"complete", "not_applicable"}
                    for section in {"resources_economy", "progression_balance", "tutorial"}
                ),
                "resource_model": report.get("resource_model") is not None,
                "benchmark_evidence": benchmark_complete,
            }
            archive_results.append(
                {"report_id": report.get("id"), "slug": report.get("slug"), "checks": checks, "complete": all(checks.values())}
            )

        transport_ok = all(item["passed"] for item in response_checks)
        performance_ok = all(item["passed"] for item in performance_checks)
        responsive_ok = all(item["passed"] for item in responsive_checks)
        archive_complete = bool(archive_results) and all(item["complete"] for item in archive_results)
        result = {
            "schema": "game-observatory.public-site-quality.v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "base_url": base_url,
            "ok": transport_ok and performance_ok and responsive_ok and browser_ok and archive_complete,
            "site_shell_ready": transport_ok and performance_ok and responsive_ok and browser_ok,
            "archive_complete": archive_complete,
            "budget": asdict(self.budget),
            "measurements": {
                "latency_samples_ms": latency_samples,
                "local_p95_ms": _percentile(latency_samples, 0.95),
                "browser_navigation_samples_ms": (browser_evidence or {}).get("navigation_samples_ms", []),
                "browser_p95_ms": browser_p95,
                "payload_bytes": {name: item["bytes"] for name, item in first.items()},
                "semantic_report_bytes": [item["bytes"] for item in semantic],
                "concurrent_wall_ms": concurrent_wall_ms,
            },
            "transport_checks": response_checks,
            "performance_checks": performance_checks,
            "responsive_checks": responsive_checks,
            "accessibility_checks": browser_checks,
            "archive_results": archive_results,
            "boundary": (
                "Performance timings are local/LAN facility measurements, not public-internet Web Vitals. "
                "Responsive evidence combines live desktop DOM with explicit narrow-width CSS contracts."
            ),
        }
        if write:
            output = self.store.export_root / "public-site-quality-validation.json"
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["path"] = str(output)
        return result
