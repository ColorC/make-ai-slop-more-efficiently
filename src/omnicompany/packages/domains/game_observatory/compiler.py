from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from .canonical_graph import design_object_rows
from .design_renderer import DesignSpecRenderer
from .media_validation import assert_public_artifacts
from .models import Claim, ContentKind, GameReport


class SemanticReportCompiler:
    """Compile canonical reports into public JSON and semantic HTML views."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _derived_claims(report: GameReport) -> list[Claim]:
        claims = list(report.claims)
        known = {item.id for item in claims}
        for node in report.flow:
            claim_id = f"claim.{report.id}.flow.{node.id}"
            if claim_id not in known:
                claims.append(
                    Claim(
                        id=claim_id,
                        kind=ContentKind.direct_observation,
                        statement=node.description,
                        source_ids=node.source_ids,
                        artifact_ids=node.artifact_ids,
                        flow_node_id=node.id,
                        review_status="reviewed" if node.source_ids or node.artifact_ids else "pending",
                    )
                )
        for mechanism in report.mechanisms:
            claim_id = f"claim.{report.id}.mechanism.{mechanism.id}"
            if claim_id not in known:
                claims.append(
                    Claim(
                        id=claim_id,
                        kind=ContentKind.direct_observation,
                        statement=mechanism.description,
                        source_ids=mechanism.source_ids,
                        review_status="reviewed" if mechanism.source_ids else "pending",
                    )
                )
        for observation in report.observations:
            if isinstance(observation, str):
                continue
            claim_id = f"claim.{report.id}.observation.{observation.id}"
            if claim_id not in known:
                claims.append(
                    Claim(
                        id=claim_id,
                        kind=ContentKind.direct_observation,
                        statement=observation.statement,
                        source_ids=observation.source_ids,
                        artifact_ids=observation.artifact_ids,
                        run_id=observation.run_id,
                        review_status="reviewed",
                    )
                )
        for interpretation in report.interpretations:
            if isinstance(interpretation, Claim) and interpretation.id not in known:
                claims.append(interpretation)
        return claims

    @classmethod
    def public_report(cls, report: GameReport) -> dict[str, Any]:
        report.assert_publishable()
        assert_public_artifacts(report)
        payload = report.model_dump(mode="json")
        public_sources: list[dict[str, Any]] = []
        for source in payload["sources"]:
            if source.get("status") == "retracted":
                public_sources.append(
                    {
                        "id": source["id"],
                        "kind": source["kind"],
                        "title": source["title"],
                        "url": "#source-retracted",
                        "locator": None,
                        "author": None,
                        "published_at": None,
                        "captured_at": source["captured_at"],
                        "version_context": source.get("version_context"),
                        "public": False,
                        "note": "该来源已撤回；canonical history 保留撤回事件，公开投影不再展示内容。",
                        "status": "retracted",
                        "retracted_at": source.get("retracted_at"),
                        "retraction_reason": source.get("retraction_reason"),
                    }
                )
            elif source.get("public"):
                public_sources.append(source)
            else:
                public_sources.append(
                    {
                        "id": source["id"],
                        "kind": source["kind"],
                        "title": source["title"],
                        "url": "#internal-source-withheld",
                        "locator": None,
                        "author": None,
                        "published_at": None,
                        "captured_at": source["captured_at"],
                        "version_context": source.get("version_context"),
                        "public": False,
                        "note": "内部源码定位已保留在 canonical store，公开投影不暴露本机路径。",
                        "status": "active",
                        "retracted_at": None,
                        "retraction_reason": None,
                    }
                )
        payload["sources"] = public_sources
        public_source_by_id = {
            item["id"]: item
            for item in public_sources
            if item.get("public") is True and item.get("status") == "active"
        }
        payload["artifacts"] = [
            {
                **item,
                "path": f"/api/game-observatory/artifacts/{item['id']}",
                "metadata": {
                    key: value
                    for key, value in item.get("metadata", {}).items()
                    if key not in {"origin_path", "adb_path", "local_path"}
                },
            }
            for item in payload["artifacts"]
            if item.get("metadata", {}).get("public")
        ]
        source_policies = {item.id: item.usage_policy for item in report.sources}
        payload["player_voices"] = [
            {
                **item,
                "quote": (
                    item.get("quote")
                    if source_policies.get(item.get("source_id")) == "short_excerpt"
                    else None
                ),
                "context": None,
            }
            for item in payload["player_voices"]
            if item.get("status") == "active" and item.get("review_status") == "reviewed"
        ]
        payload["community_feedback"] = [
            {
                **item,
                "source": public_source_by_id[item["source"]["id"]],
            }
            for item in payload.get("community_feedback", [])
            if item.get("source", {}).get("id") in public_source_by_id
        ]
        payload["claims"] = [
            item.model_dump(mode="json")
            for item in cls._derived_claims(report)
            if item.review_status != "retracted"
        ]
        design_objects = design_object_rows(report)
        payload["compiled"] = {
            "schema": "game-observatory.public-design-spec.v0.3",
            "canonical_report_id": report.id,
            "canonical_design_spec_id": report.design_spec.id if report.design_spec else None,
            "stable_url": f"/game-observatory/reports/{report.slug}",
            "reader_url": f"/game-observatory/report/{report.slug}",
            "diagrams": {
                "core_loop": (
                    f"/api/game-observatory/diagrams/{report.slug}/core-loop.svg"
                ),
                "navigation": (
                    f"/api/game-observatory/diagrams/{report.slug}/navigation.svg"
                ),
                "interaction": (
                    f"/api/game-observatory/diagrams/{report.slug}/interaction.svg"
                ),
            },
            "object_index": [
                {
                    "object_type": object_type,
                    "object_id": object_id,
                    "label": (
                        body.get("title")
                        or body.get("name")
                        or body.get("section")
                        or object_id
                    ),
                }
                for object_type, object_id, body in design_objects
            ],
            "object_counts": {
                object_type: sum(
                    1 for candidate_type, _object_id, _body in design_objects
                    if candidate_type == object_type
                )
                for object_type in sorted({item[0] for item in design_objects})
            },
        }
        return payload

    @staticmethod
    def diff(before: GameReport, after: GameReport) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []

        def walk(left: Any, right: Any, path: str) -> None:
            if left == right:
                return
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(set(left) | set(right)):
                    child = f"{path}/{key}"
                    if key not in left:
                        changes.append({"op": "add", "path": child, "before": None, "after": right[key]})
                    elif key not in right:
                        changes.append({"op": "remove", "path": child, "before": left[key], "after": None})
                    else:
                        walk(left[key], right[key], child)
                return
            if isinstance(left, list) and isinstance(right, list):
                keyed = all(isinstance(item, dict) and "id" in item for item in [*left, *right])
                if keyed:
                    left_map = {item["id"]: item for item in left}
                    right_map = {item["id"]: item for item in right}
                    walk(left_map, right_map, path)
                else:
                    changes.append({"op": "replace", "path": path or "/", "before": left, "after": right})
                return
            changes.append({"op": "replace", "path": path or "/", "before": left, "after": right})

        walk(before.model_dump(mode="json"), after.model_dump(mode="json"), "")
        return changes

    @classmethod
    def semantic_html(cls, report: GameReport) -> str:
        payload = cls.public_report(report)
        return DesignSpecRenderer.semantic_html(report, payload)

    @staticmethod
    def diagram_svg(report: GameReport, kind: str) -> str:
        return DesignSpecRenderer.diagram_svg(report, kind)

    @classmethod
    def _legacy_semantic_html(cls, report: GameReport) -> str:
        """Deprecated v0.2 article renderer kept temporarily for migration diffs."""
        payload = cls.public_report(report)
        e = lambda value: html.escape(str(value), quote=True)
        cover = next(
            (item for item in payload["artifacts"] if item["id"] == payload.get("cover_artifact_id")),
            None,
        )
        cover_html = (
            f'<figure><img src="{e(cover["path"])}" alt="{e(report.system_title)} 运行证据">'
            f'<figcaption>{e(report.scope.device)}</figcaption></figure>'
            if cover
            else ""
        )
        flow_html = "".join(
            f'<li id="{e(node.id)}"><h3>{e(node.title)}</h3><p>{e(node.description)}</p>'
            f'<code>{e(node.action or "")}</code></li>'
            for node in report.flow
        )
        public_artifacts = {item["id"]: item for item in payload["artifacts"]}
        surface_html = "".join(
            (
                f'<article id="{e(surface["id"])}"><h3>{e(surface["title"])}</h3>'
                f'<p><small>{e(surface["kind"])}</small> {e(surface.get("description") or "")}</p>'
                + (
                    f'<figure><img src="{e(public_artifacts[surface["artifact_ids"][0]]["path"])}" '
                    f'alt="{e(surface["title"])} 运行证据"></figure>'
                    if surface.get("artifact_ids")
                    and surface["artifact_ids"][0] in public_artifacts
                    else '<p><em>来源约束的语义布局；当前没有可公开画面。</em></p>'
                )
                + '<ul>'
                + "".join(
                    f'<li id="{e(item["id"])}"><strong>{e(item.get("label") or item.get("text") or item["role"])}</strong>'
                    f' <small>{e(item["role"])}'
                    f'{" · " + e(" / ".join(item.get("actions", []))) if item.get("actions") else ""}</small></li>'
                    for item in surface.get("elements", [])
                )
                + '</ul></article>'
            )
            for surface in payload["surfaces"]
        ) or "<p>当前档案没有已发布的页面结构。</p>"
        mechanism_html = "".join(
            f'<section id="{e(item.id)}"><h3>{e(item.title)}</h3><p>{e(item.description)}</p>'
            f'<pre><code>{e(item.code or "")}</code></pre></section>'
            for item in report.mechanisms
        )
        voice_html = "".join(
            f'<blockquote id="{e(item.id)}"><p>{e(item.summary)}</p><footer>{e(item.theme)}</footer></blockquote>'
            for item in report.player_voices
        ) or "<p>当前没有进入公开投影的玩家声音。</p>"
        source_html = "".join(
            (
                f'<li id="{e(item["id"])}"><span>来源已撤回</span>'
                f'<small>{e(item["kind"])} · {e(item.get("version_context") or "unknown")} · '
                "历史 tombstone 保留</small></li>"
                if item.get("status") == "retracted"
                else f'<li id="{e(item["id"])}"><a href="{e(item["url"])}">{e(item["title"])}</a>'
                f'<small>{e(item["kind"])} · {e(item.get("version_context") or "unknown")}</small></li>'
            )
            for item in payload["sources"]
        )
        claim_html = "".join(
            f'<li id="{e(item["id"])}" data-kind="{e(item["kind"])}">{e(item["statement"])}</li>'
            for item in payload["claims"]
        )
        resource_html = "".join(
            f'<li id="{e(item["id"])}"><strong>{e(item["resource"])}</strong>'
            f' <small>{e(item["role"])}</small><p>{e(item["description"])}</p></li>'
            for item in payload["resources"]
        ) or "<li>该系统没有经济资源关系。</li>"
        observation_html = "".join(
            f'<li id="{e(item["id"])}">{e(item["statement"])}</li>'
            for item in payload["observations"]
        )
        interpretation_html = "".join(
            f'<li id="{e(item["id"])}">{e(item["statement"])}</li>'
            for item in payload["interpretations"]
        )
        scope_rows = "".join(
            f'<tr><th>{e(key)}</th><td>{e(payload["scope"].get(key) or "unknown")}</td></tr>'
            for key in ("platform", "version", "region", "locale", "device", "account_stage", "captured_at")
        )
        canonical_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(report.system_title)} · 游戏观测站</title><meta name="description" content="{e(report.summary)}">
<style>body{{max-width:980px;margin:auto;padding:3rem 1.25rem;font:17px/1.7 system-ui;color:#17283a;background:#f6f1e7}}h1{{font:clamp(2.2rem,6vw,4.8rem)/1.05 Georgia,serif}}img{{max-width:100%;height:auto}}section,nav{{margin:3rem 0}}article{{margin:1.5rem 0;padding:1.25rem;border:1px solid #c8c1b5}}small{{display:block;color:#64717d}}pre{{overflow:auto;background:#172f45;color:#eef4f6;padding:1rem}}table{{width:100%;border-collapse:collapse}}th,td{{padding:.5rem;border-bottom:1px solid #d7d0c5;text-align:left;vertical-align:top}}a{{color:#a64c32}}@media(max-width:680px){{body{{padding:1.5rem 1rem}}}}</style>
<script type="application/ld+json">{canonical_json}</script></head>
<body><nav><a href="/game-observatory/">← 游戏观测站</a></nav>
 <main itemscope itemtype="https://schema.org/TechArticle"><header><p>{e(report.game_title)} · {e(report.scope.version)}</p>
 <h1 itemprop="headline">{e(report.system_title)}</h1><p itemprop="abstract">{e(report.summary)}</p></header>{cover_html}
 <section id="scope"><h2>观察范围</h2><table>{scope_rows}</table></section>
 <section id="surfaces"><h2>页面与 UI</h2>{surface_html}</section>
 <section id="player-journey"><h2>玩家旅程</h2><ol>{flow_html}</ol></section>
 <section id="mechanisms"><h2>机制表达</h2>{mechanism_html}</section>
 <section id="resources"><h2>资源关系</h2><ul>{resource_html}</ul></section>
 <section id="player-voices"><h2>玩家声音</h2>{voice_html}</section>
 <section id="observations"><h2>观察</h2><ul>{observation_html}</ul></section>
 <section id="interpretations"><h2>分析解释</h2><ul>{interpretation_html}</ul></section>
<section id="claims"><h2>可引用结论</h2><ul>{claim_html}</ul></section>
<section id="sources"><h2>来源</h2><ul>{source_html}</ul></section></main></body></html>"""

    def compile(self, reports: list[GameReport], *, base_url: str = "http://127.0.0.1:8210") -> dict[str, Any]:
        public_reports = [self.public_report(item) for item in reports]
        compiler_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        manifest_path = self.output_root / ".compile-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {"schema": "game-observatory.compile-manifest.v2", "reports": {}}
        compiler_changed = manifest.get("compiler_sha256") != compiler_sha256
        previous_reports = manifest.get("reports", {}) if isinstance(manifest, dict) else {}
        next_reports: dict[str, Any] = {}
        compiled: list[str] = []
        skipped: list[str] = []
        changed_sections: dict[str, list[str]] = {}
        for report, payload in zip(reports, public_reports, strict=True):
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            report_hash = hashlib.sha256(encoded).hexdigest()
            section_hashes = {
                key: hashlib.sha256(
                    json.dumps(payload.get(key), ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                for key in (
                    "contract_version",
                    "migration_status",
                    "design_spec",
                    "summary",
                    "scope",
                    "game",
                    "system_concept",
                    "system_instance",
                    "surfaces",
                    "artifacts",
                    "runs",
                    "flow",
                    "mechanisms",
                    "resources",
                    "resource_model",
                    "observations",
                    "interpretations",
                    "open_questions",
                    "player_voices",
                    "claims",
                    "sources",
                    "benchmark_task",
                )
            }
            previous = previous_reports.get(report.slug, {})
            json_path = self.output_root / f"{report.slug}.json"
            html_path = self.output_root / f"{report.slug}.html"
            diagram_paths = {
                kind: self.output_root / f"{report.slug}.{kind}.svg"
                for kind in ("core-loop", "navigation", "interaction")
            }
            changed = [
                key
                for key, value in section_hashes.items()
                if previous.get("sections", {}).get(key) != value
            ]
            if compiler_changed:
                changed.insert(0, "compiler")
            if (
                not compiler_changed
                and previous.get("sha256") == report_hash
                and json_path.is_file()
                and html_path.is_file()
                and all(path.is_file() for path in diagram_paths.values())
            ):
                skipped.append(report.slug)
            else:
                json_tmp = json_path.with_suffix(".json.tmp")
                html_tmp = html_path.with_suffix(".html.tmp")
                json_tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                html_tmp.write_text(self.semantic_html(report), encoding="utf-8")
                json_tmp.replace(json_path)
                html_tmp.replace(html_path)
                for kind, path in diagram_paths.items():
                    svg_tmp = path.with_suffix(".svg.tmp")
                    svg_tmp.write_text(self.diagram_svg(report, kind), encoding="utf-8")
                    svg_tmp.replace(path)
                compiled.append(report.slug)
            changed_sections[report.slug] = changed
            next_reports[report.slug] = {"sha256": report_hash, "sections": section_hashes}
        catalog = {"schema": "game-observatory.public-design-spec-catalog.v0.3", "reports": public_reports}
        catalog_path = self.output_root / "catalog.json"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        urls = [f"{base_url.rstrip('/')}/game-observatory/reports/{item.slug}" for item in reports]
        sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        sitemap += "\n".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls)
        sitemap += "\n</urlset>\n"
        sitemap_path = self.output_root / "sitemap.xml"
        sitemap_path.write_text(sitemap, encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "game-observatory.compile-manifest.v2",
                    "compiler_sha256": compiler_sha256,
                    "reports": next_reports,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        digest = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        return {
            "reports": len(reports),
            "compiled": compiled,
            "skipped": skipped,
            "changed_sections": changed_sections,
            "catalog": str(catalog_path),
            "sitemap": str(sitemap_path),
            "manifest": str(manifest_path),
            "compiler_sha256": compiler_sha256,
            "sha256": digest,
        }
