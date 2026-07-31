from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

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
        for index, statement in enumerate(report.interpretations, start=1):
            claim_id = f"claim.{report.id}.interpretation.{index}"
            if claim_id not in known:
                claims.append(
                    Claim(
                        id=claim_id,
                        kind=ContentKind.analyst_interpretation,
                        statement=statement,
                        review_status="reviewed",
                    )
                )
        return claims

    @classmethod
    def public_report(cls, report: GameReport) -> dict[str, Any]:
        payload = report.model_dump(mode="json")
        payload["sources"] = [
            source
            if source.get("public")
            else {
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
            }
            for source in payload["sources"]
        ]
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
        payload["claims"] = [item.model_dump(mode="json") for item in cls._derived_claims(report)]
        payload["compiled"] = {
            "schema": "game-observatory.public-report.v1",
            "canonical_report_id": report.id,
            "stable_url": f"/game-observatory/reports/{report.slug}",
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
            f'<li id="{e(item["id"])}"><a href="{e(item["url"])}">{e(item["title"])}</a>'
            f'<small>{e(item["kind"])} · {e(item.get("version_context") or "unknown")}</small></li>'
            for item in payload["sources"]
        )
        claim_html = "".join(
            f'<li id="{e(item["id"])}" data-kind="{e(item["kind"])}">{e(item["statement"])}</li>'
            for item in payload["claims"]
        )
        canonical_json = html.escape(json.dumps(payload, ensure_ascii=False), quote=False)
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(report.system_title)} · 游戏观测站</title><meta name="description" content="{e(report.summary)}">
<style>body{{max-width:980px;margin:auto;padding:3rem 1.25rem;font:17px/1.7 system-ui;color:#17283a;background:#f6f1e7}}h1{{font:clamp(2.2rem,6vw,4.8rem)/1.05 Georgia,serif}}img{{max-width:100%;height:auto}}section,nav{{margin:3rem 0}}small{{display:block;color:#64717d}}pre{{overflow:auto;background:#172f45;color:#eef4f6;padding:1rem}}a{{color:#a64c32}}</style>
<script type="application/ld+json">{canonical_json}</script></head>
<body><nav><a href="/game-observatory/">← 游戏观测站</a></nav>
<main itemscope itemtype="https://schema.org/TechArticle"><header><p>{e(report.game_title)} · {e(report.scope.version)}</p>
<h1 itemprop="headline">{e(report.system_title)}</h1><p itemprop="abstract">{e(report.summary)}</p></header>{cover_html}
<section id="player-journey"><h2>玩家旅程</h2><ol>{flow_html}</ol></section>
<section id="mechanisms"><h2>机制表达</h2>{mechanism_html}</section>
<section id="player-voices"><h2>玩家声音</h2>{voice_html}</section>
<section id="claims"><h2>可引用结论</h2><ul>{claim_html}</ul></section>
<section id="sources"><h2>来源</h2><ul>{source_html}</ul></section></main></body></html>"""

    def compile(self, reports: list[GameReport], *, base_url: str = "http://127.0.0.1:8210") -> dict[str, Any]:
        public_reports = [self.public_report(item) for item in reports]
        for report, payload in zip(reports, public_reports, strict=True):
            (self.output_root / f"{report.slug}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (self.output_root / f"{report.slug}.html").write_text(
                self.semantic_html(report), encoding="utf-8"
            )
        catalog = {"schema": "game-observatory.public-catalog.v1", "reports": public_reports}
        catalog_path = self.output_root / "catalog.json"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        urls = [f"{base_url.rstrip('/')}/game-observatory/reports/{item.slug}" for item in reports]
        sitemap = '<?xml version="1.0" encoding="UTF-8"?>\
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\
'
        sitemap += "\
".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls)
        sitemap += "\
</urlset>\
"
        sitemap_path = self.output_root / "sitemap.xml"
        sitemap_path.write_text(sitemap, encoding="utf-8")
        digest = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        return {
            "reports": len(reports),
            "catalog": str(catalog_path),
            "sitemap": str(sitemap_path),
            "sha256": digest,
        }