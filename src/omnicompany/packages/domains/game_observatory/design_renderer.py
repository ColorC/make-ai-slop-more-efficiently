from __future__ import annotations

import html
import json
from typing import Any, Iterable

from .models import GameReport


class DesignSpecRenderer:
    """Render the v0.3 design-spec contract without turning it into an article."""

    CHAPTERS = (
        ("overview", "系统概述"),
        ("goals", "玩家目标与入口"),
        ("core-loop", "核心循环"),
        ("information-architecture", "信息架构与页面"),
        ("interaction", "交互与状态"),
        ("rules", "机制与资源"),
        ("progression", "成长与数值"),
        ("feedback", "反馈与教学"),
        ("failure", "失败恢复与依赖"),
        ("player-voice", "玩家反馈"),
        ("sources", "版本与来源"),
    )

    @staticmethod
    def _e(value: Any) -> str:
        return html.escape(str(value), quote=True)

    @classmethod
    def _refs(cls, source_ids: Iterable[str], artifact_ids: Iterable[str]) -> str:
        values = [
            *(f'<a href="#source-{cls._e(item)}">来源 · {cls._e(item)}</a>' for item in source_ids),
            *(
                f'<a href="#artifact-{cls._e(item)}">证据 · {cls._e(item)}</a>'
                for item in artifact_ids
            ),
        ]
        return f'<div class="refs">{"".join(values)}</div>' if values else ""

    @classmethod
    def _statements(cls, values: list[dict[str, Any]]) -> str:
        return "".join(
            f'<article class="statement" id="{cls._e(item["id"])}">'
            f'<h3>{cls._e(item["title"])}</h3><p>{cls._e(item["statement"])}</p>'
            f'{cls._refs(item.get("source_ids", []), item.get("artifact_ids", []))}</article>'
            for item in values
        )

    @classmethod
    def _artifact_figure(
        cls,
        artifact: dict[str, Any] | None,
        caption: str,
        *,
        figure_id: str = "",
    ) -> str:
        if artifact is None:
            return ""
        return (
            f'<figure class="evidence" id="{cls._e(figure_id or "artifact-" + artifact["id"])}">'
            f'<img src="{cls._e(artifact["path"])}" alt="{cls._e(caption)}" loading="lazy">'
            f'<figcaption>{cls._e(caption)} · {cls._e(artifact["id"])}</figcaption></figure>'
        )

    @classmethod
    def semantic_html(cls, report: GameReport, payload: dict[str, Any]) -> str:
        spec = payload["design_spec"]
        artifacts = {item["id"]: item for item in payload["artifacts"]}
        surfaces = {item["id"]: item for item in payload["surfaces"]}
        sources = {item["id"]: item for item in payload["sources"]}
        design_artifacts = {
            item["artifact_id"]: item for item in spec["design_artifacts"]
        }
        coverage = {item["section"]: item for item in spec["section_coverage"]}
        canonical_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

        nav = "".join(
            f'<a href="#{cls._e(anchor)}">{cls._e(label)}</a>'
            for anchor, label in cls.CHAPTERS
        )
        coverage_html = "".join(
            f'<li><span>{cls._e(key)}</span><strong>{cls._e(item["status"])}</strong>'
            f'<small>{cls._e(item["rationale"])}</small></li>'
            for key, item in coverage.items()
        )
        cover = artifacts.get(payload.get("cover_artifact_id"))
        cover_html = cls._artifact_figure(cover, f'{spec["title"]}真实运行画面')

        core_steps = "".join(
            f'<li id="{cls._e(step["id"])}"><span>{index:02d}</span><div>'
            f'<h3>{cls._e(step["title"])}</h3><dl>'
            f'<dt>玩家动作</dt><dd>{cls._e(step["player_action"])}</dd>'
            f'<dt>系统响应</dt><dd>{cls._e(step["system_response"])}</dd>'
            f'<dt>状态变化</dt><dd>{cls._e(step["state_before"])} → {cls._e(step["state_after"])}</dd>'
            f'</dl>{cls._refs(step.get("source_ids", []), step.get("artifact_ids", []))}'
            f'</div></li>'
            for index, step in enumerate(spec["core_loop"]["steps"], start=1)
        )
        flow_trace = "".join(
            f'<li id="{cls._e(node["id"])}"><b>{index:02d} · {cls._e(node["title"])}</b>'
            f'<span>{cls._e(node["description"])}</span>'
            f'{cls._refs(node.get("source_ids", []), node.get("artifact_ids", []))}</li>'
            for index, node in enumerate(payload["flow"], start=1)
        )

        surface_html: list[str] = []
        layouts = {item["surface_id"]: item for item in spec["layout_specs"]}
        for surface_id in spec["information_architecture"]["surface_ids"]:
            surface = surfaces[surface_id]
            layout = layouts[surface_id]
            screenshot = next(
                (artifacts.get(item) for item in surface.get("artifact_ids", []) if artifacts.get(item)),
                None,
            )
            derivative = next(
                (
                    artifacts.get(item["artifact_id"])
                    for item in spec["design_artifacts"]
                    if surface_id in item.get("surface_ids", [])
                    and item["kind"] in {"annotated_plate", "layout_spec", "wireframe"}
                ),
                None,
            )
            element_by_id = {item["id"]: item for item in surface["elements"]}
            rows = "".join(
                f'<tr id="{cls._e(item["id"])}"><td>{cls._e(element_by_id[item["ui_element_id"]].get("label") or element_by_id[item["ui_element_id"]]["role"])}</td>'
                f'<td><code>{item["bounds"]["x"]:.3f}, {item["bounds"]["y"]:.3f}, '
                f'{item["bounds"]["width"]:.3f}, {item["bounds"]["height"]:.3f}</code></td>'
                f'<td>{cls._e(" / ".join(item.get("anchors", [])) or "—")}</td></tr>'
                for item in layout["elements"]
            )
            surface_html.append(
                f'<article class="surface" id="{cls._e(surface_id)}"><header><span>{cls._e(surface["kind"])}</span>'
                f'<h3>{cls._e(surface["title"])}</h3><p>{cls._e(surface.get("description") or "")}</p></header>'
                f'<div class="visual-pair">{cls._artifact_figure(screenshot, "真实游戏画面")}'
                f'{cls._artifact_figure(derivative, "由画面反推的页面设计稿")}</div>'
                f'<table><caption>机器可读布局 · {cls._e(layout["canvas_aspect_ratio"])}</caption>'
                f'<thead><tr><th>元素</th><th>归一化边界 x, y, w, h</th><th>锚点</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>{cls._refs(layout.get("source_ids", []), layout.get("artifact_ids", []))}</article>'
            )

        navigation_rows = "".join(
            f'<tr id="{cls._e(edge["id"])}"><td>{cls._e(surfaces[edge["from_surface_id"]]["title"])}</td>'
            f'<td>{cls._e(edge["trigger"])}</td><td>{cls._e(surfaces[edge["to_surface_id"]]["title"])}</td>'
            f'<td>{cls._e(edge.get("condition") or "始终")}</td></tr>'
            for edge in spec["information_architecture"]["edges"]
        )

        interactions = "".join(
            f'<article class="interaction" id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3>'
            f'<p><b>触发：</b>{cls._e(item["trigger"])}</p><ol>'
            + "".join(
                f'<li id="{cls._e(step["id"])}"><b>{step["order"]}. {cls._e(step["actor"])}</b> '
                f'{cls._e(step["action"])}<small>{cls._e(step.get("response") or "")}</small>'
                f'{cls._refs(step.get("source_ids", []), step.get("artifact_ids", []))}</li>'
                for step in item["steps"]
            )
            + f'</ol><p><b>完成后：</b>{cls._e("；".join(item["postconditions"]))}</p>'
            f'{cls._refs(item.get("source_ids", []), item.get("artifact_ids", []))}</article>'
            for item in spec["interaction_specs"]
        )
        state_tables = "".join(
            f'<article class="state-matrix" id="{cls._e(matrix["id"])}"><h3>{cls._e(matrix["title"])}</h3>'
            f'<p>对象：<a href="#{cls._e(matrix["subject_id"])}">{cls._e(matrix["subject_id"])}</a></p>'
            f'<table><thead><tr><th>状态</th><th>条件</th><th>可见/可用</th><th>反馈</th><th>下一状态</th></tr></thead><tbody>'
            + "".join(
                f'<tr id="{cls._e(case["id"])}"><td>{cls._e(case["state"])}</td><td>{cls._e(case["condition"])}</td>'
                f'<td>{cls._e(case.get("visible"))} / {cls._e(case.get("enabled"))}</td>'
                f'<td>{cls._e("；".join(case.get("feedback", [])))}</td><td>{cls._e(case.get("next_state") or "—")}</td></tr>'
                for case in matrix["cases"]
            )
            + "</tbody></table></article>"
            for matrix in spec["state_matrices"]
        )

        mechanisms = "".join(
            f'<article class="rule" id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3>'
            f'<p>{cls._e(item["description"])}</p>'
            + (
                f'<pre><code>{cls._e(item.get("code"))}</code></pre>'
                if item.get("code")
                else ""
            )
            + "</article>"
            for item in payload["mechanisms"]
        )
        resources = "".join(
            f'<tr id="{cls._e(item["id"])}"><td>{cls._e(item["resource"])}</td><td>{cls._e(item["role"])}</td>'
            f'<td>{cls._e(item["description"])}</td></tr>'
            for item in payload["resources"]
        )
        progression = "".join(
            f'<article id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3>'
            + "".join(
                f'<dl id="{cls._e(axis["id"])}"><dt>{cls._e(axis["name"])}</dt>'
                f'<dd>{cls._e(" → ".join(axis["stages"]))} · {cls._e(axis["unit"])}</dd>'
                f'<dt>门槛</dt><dd>{cls._e("；".join(axis["gates"]) or "无")}</dd></dl>'
                for axis in item["axes"]
            )
            + "</article>"
            for item in spec["progression_specs"]
        )
        balances = "".join(
            f'<article id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3><p>{cls._e(item["target_experience"])}</p><table>'
            f'<thead><tr><th>参数</th><th>值/范围</th><th>调节作用</th></tr></thead><tbody>'
            + "".join(
                f'<tr id="{cls._e(parameter["id"])}"><td>{cls._e(parameter["name"])}</td>'
                f'<td>{cls._e(parameter["value_or_range"])} {cls._e(parameter.get("unit") or "")}</td>'
                f'<td>{cls._e(parameter["tuning_role"])}</td></tr>'
                for parameter in item["parameters"]
            )
            + "</tbody></table></article>"
            for item in spec["balance_specs"]
        )
        feedback = "".join(
            f'<article id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3>'
            f'<p><b>{cls._e(item["trigger"])}</b> · {cls._e(" / ".join(item["channels"]))} · {cls._e(item["timing"])}</p>'
            f'<dl><dt>成功</dt><dd>{cls._e(item["success_behavior"])}</dd><dt>失败</dt><dd>{cls._e(item["failure_behavior"])}</dd></dl></article>'
            for item in spec["feedback_specs"]
        )
        tutorials = "".join(
            f'<article id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3><ol>'
            + "".join(
                f'<li id="{cls._e(step["id"])}"><b>{cls._e(step["trigger"])}</b> · {cls._e(step["instruction"])}'
                f'<small>完成条件：{cls._e(step["completion_condition"])}；恢复：{cls._e(step["recovery"])}</small></li>'
                for step in item["steps"]
            )
            + "</ol></article>"
            for item in spec["tutorial_specs"]
        )
        failures = "".join(
            f'<article id="{cls._e(item["id"])}"><h3>{cls._e(item["title"])}</h3><dl>'
            f'<dt>失败条件</dt><dd>{cls._e(item["failure_condition"])}</dd>'
            f'<dt>可见反馈</dt><dd>{cls._e(item["visible_behavior"])}</dd>'
            f'<dt>保留状态</dt><dd>{cls._e(item["retained_state"])}</dd>'
            f'<dt>恢复动作</dt><dd>{cls._e(item["recovery_action"])}</dd></dl></article>'
            for item in spec["failure_recovery_specs"]
        )
        dependencies = "".join(
            f'<li id="{cls._e(item["id"])}"><b>{cls._e(item["title"])}</b> '
            f'<span>{cls._e(item["direction"])} → {cls._e(item["target_system_id"])}</span>'
            f'<p>{cls._e(item["dependency"])}</p></li>'
            for item in spec["dependency_specs"]
        )
        voices = "".join(
            f'<blockquote id="{cls._e(item["id"])}"><p>{cls._e(item["summary"])}</p>'
            + (f'<q>{cls._e(item.get("quote"))}</q>' if item.get("quote") else "")
            +
            f'<footer>{cls._e(item["theme"])} · <a href="#source-{cls._e(item["source_id"])}">'
            f'{cls._e(sources[item["source_id"]]["title"])}</a> · 关联对象 {cls._e(" / ".join(item.get("target_object_ids", [])))}</footer></blockquote>'
            for item in payload["player_voices"]
        )
        source_html = "".join(
            f'<li id="source-{cls._e(item["id"])}"><b>{cls._e(item["title"])}</b>'
            f'<small>{cls._e(item["kind"])} · {cls._e(item.get("version_context") or "版本未注明")}</small>'
            + (
                f'<a href="{cls._e(item["url"])}" rel="noreferrer">访问来源</a>'
                if item.get("public") and str(item.get("url", "")).startswith(("http://", "https://"))
                else "<span>定位已保留，公开投影不暴露内部路径</span>"
            )
            + "</li>"
            for item in payload["sources"]
        )

        diagrams = payload["compiled"]["diagrams"]
        return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{cls._e(spec["title"])} · 反推游戏设计案</title><meta name="description" content="{cls._e(report.summary)}">
<style>
:root{{--paper:#f3efe6;--panel:#fffdf7;--ink:#182936;--muted:#64717a;--line:#cfc8bb;--accent:#e45f36;--navy:#102b3b}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 Inter,"Noto Sans SC",system-ui,sans-serif}}a{{color:#9d3e24}}nav.top{{position:sticky;top:0;z-index:10;display:flex;gap:1rem;align-items:center;padding:.85rem 1.4rem;background:rgba(243,239,230,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(12px)}}nav.top .chapters{{display:flex;gap:.75rem;overflow:auto;white-space:nowrap}}nav.top>a:first-child{{font-weight:800;color:var(--navy);text-decoration:none}}main{{max-width:1320px;margin:auto;padding:3rem 1.4rem 7rem}}header.hero{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:3rem;align-items:end;margin-bottom:4rem}}.kicker{{letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}}h1{{font:clamp(2.8rem,7vw,6.8rem)/.95 Georgia,"Noto Serif SC",serif;margin:.4rem 0 1.5rem}}h2{{font:clamp(2rem,4vw,3.6rem)/1.1 Georgia,"Noto Serif SC",serif;margin:.2rem 0 1.5rem}}h3{{line-height:1.25}}.summary{{font-size:1.2rem;max-width:58ch}}.meta{{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.meta div{{background:var(--panel);padding:1rem}}.meta span,.meta small{{display:block;color:var(--muted)}}section.chapter{{padding:4rem 0;border-top:1px solid var(--line)}}.chapter-head{{display:grid;grid-template-columns:180px 1fr;gap:1.5rem}}.chapter-head span{{color:var(--accent);font-weight:800}}.coverage{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1px;background:var(--line);padding:1px;list-style:none}}.coverage li,.statement,.rule,.interaction,.state-matrix,.surface,.chapter article{{background:var(--panel);padding:1.2rem;border:1px solid var(--line)}}.coverage span,.coverage small{{display:block;color:var(--muted)}}.coverage strong{{color:#18725f}}.statement-grid,.card-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}}figure.evidence{{margin:0;background:#121b22;color:#eef4f6}}figure.evidence img{{display:block;width:100%;height:auto;max-height:720px;object-fit:contain}}figure.evidence figcaption{{padding:.65rem 1rem;font-size:.8rem}}.diagram{{display:block;width:100%;max-height:720px;border:1px solid var(--line);background:var(--panel)}}.core-steps{{list-style:none;padding:0;counter-reset:none}}.core-steps li{{display:grid;grid-template-columns:70px 1fr;gap:1rem;margin:1rem 0}}.core-steps>li>span{{font:2.6rem/1 Georgia;color:var(--accent)}}dl{{display:grid;grid-template-columns:minmax(100px,.25fr) 1fr;gap:.35rem 1rem}}dt{{font-weight:800}}dd{{margin:0}}.refs{{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.8rem}}.refs a{{font-size:.75rem;padding:.2rem .45rem;border:1px solid var(--line);text-decoration:none}}.surface{{margin:2rem 0;padding:1.4rem}}.surface>header span{{color:var(--accent);font-size:.8rem;text-transform:uppercase}}.visual-pair{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;margin:1rem 0}}table{{width:100%;border-collapse:collapse;overflow:auto;display:table}}caption{{text-align:left;font-weight:800;padding:.8rem 0}}th,td{{padding:.65rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}pre{{overflow:auto;background:var(--navy);color:#eef5f7;padding:1rem}}blockquote{{margin:1rem 0;padding:1.2rem 1.4rem;border-left:5px solid var(--accent);background:var(--panel)}}blockquote footer,small{{display:block;color:var(--muted)}}.source-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem;list-style:none;padding:0}}.source-list li{{background:var(--panel);border:1px solid var(--line);padding:1rem}}.source-list a,.source-list span{{display:block;margin-top:.6rem}}@media(max-width:800px){{header.hero,.chapter-head,.visual-pair{{grid-template-columns:1fr}}nav.top .chapters{{display:none}}main{{padding-top:2rem}}table{{display:block;overflow-x:auto}}h1{{font-size:3.4rem}}}}@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
</style><script type="application/ld+json">{canonical_json}</script></head><body>
<nav class="top"><a href="/game-observatory/">← 设计案资料库</a><div class="chapters">{nav}</div></nav><main itemscope itemtype="https://schema.org/CreativeWork">
<header class="hero"><div><p class="kicker">Reverse-engineered game design specification · {cls._e(report.game_title)}</p><h1 itemprop="name">{cls._e(spec["title"])}</h1><p class="summary" itemprop="abstract">{cls._e(report.summary)}</p></div><div>{cover_html}<div class="meta"><div><span>版本</span><strong>{cls._e(report.scope.version)}</strong></div><div><span>平台</span><strong>{cls._e(report.scope.platform)}</strong></div><div><span>区域 / 语言</span><strong>{cls._e(report.scope.region)} / {cls._e(report.scope.locale)}</strong></div><div><span>采集时间</span><strong>{cls._e(report.scope.captured_at)}</strong></div></div></div></header>
<section class="chapter"><div class="chapter-head"><span>覆盖检查</span><div><h2>这份设计案包含什么</h2><ul class="coverage">{coverage_html}</ul></div></div></section>
<section class="chapter" id="overview"><div class="chapter-head"><span>01 / Overview</span><div><h2>系统概述</h2><div class="statement-grid">{cls._statements(spec["overview"])}</div></div></div></section>
<section class="chapter" id="goals"><div class="chapter-head"><span>02 / Goal & Entry</span><div><h2>玩家目标与入口</h2><div class="statement-grid">{cls._statements([*spec["player_goals"], *spec["entry_and_unlock"]])}</div></div></div></section>
<section class="chapter" id="core-loop"><div class="chapter-head"><span>03 / Core Loop</span><div><h2>{cls._e(spec["core_loop"]["title"])}</h2><p>{cls._e(spec["core_loop"]["player_goal"])}</p><img class="diagram" src="{cls._e(diagrams["core_loop"])}" alt="核心循环图"><ol class="core-steps">{core_steps}</ol><h3>观察轨迹与设计步骤映射</h3><ol class="source-list">{flow_trace}</ol></div></div></section>
<section class="chapter" id="information-architecture"><div class="chapter-head"><span>04 / IA & Screens</span><div id="surfaces"><h2>信息架构与页面设计</h2><img class="diagram" src="{cls._e(diagrams["navigation"])}" alt="页面导航图"><table><thead><tr><th>从</th><th>触发</th><th>到</th><th>条件</th></tr></thead><tbody>{navigation_rows}</tbody></table>{"".join(surface_html)}</div></div></section>
<section class="chapter" id="interaction"><div class="chapter-head"><span>05 / Interaction</span><div><h2>交互与状态</h2><img class="diagram" src="{cls._e(diagrams["interaction"])}" alt="交互步骤图"><div class="card-grid">{interactions}</div>{state_tables}</div></div></section>
<section class="chapter" id="rules"><div class="chapter-head"><span>06 / Rules & Economy</span><div><h2>机制与资源</h2><div class="card-grid">{mechanisms}</div><table><thead><tr><th>资源</th><th>角色</th><th>关系说明</th></tr></thead><tbody>{resources}</tbody></table></div></div></section>
<section class="chapter" id="progression"><div class="chapter-head"><span>07 / Progression</span><div><h2>成长与数值</h2><div class="card-grid">{progression}{balances}</div></div></div></section>
<section class="chapter" id="feedback"><div class="chapter-head"><span>08 / Feedback</span><div><h2>反馈与教学</h2><div class="card-grid">{feedback}{tutorials}</div></div></div></section>
<section class="chapter" id="failure"><div class="chapter-head"><span>09 / Recovery</span><div><h2>失败恢复与系统依赖</h2><div class="card-grid">{failures}</div><ul>{dependencies}</ul></div></div></section>
<section class="chapter" id="player-voice"><div class="chapter-head"><span>10 / Player Voice</span><div><h2>绑定到具体设计对象的玩家反馈</h2>{voices}</div></div></section>
<section class="chapter" id="sources"><div class="chapter-head"><span>11 / Provenance</span><div><h2>版本与来源</h2><div class="statement-grid">{cls._statements([*spec["version_notes"], *spec["monetization_specs"]])}</div><ul class="source-list">{source_html}</ul></div></div></section>
</main></body></html>'''

    @classmethod
    def diagram_svg(cls, report: GameReport, kind: str) -> str:
        report.assert_publishable()
        spec = report.design_spec
        if spec is None:  # pragma: no cover - guarded by assert_publishable
            raise ValueError("design spec is required")
        if kind == "core-loop":
            title = f"{spec.core_loop.title} · 核心循环"
            nodes = [
                (item.id, item.title, f"{item.player_action} → {item.system_response}")
                for item in spec.core_loop.steps
            ]
            edges = [(nodes[index][0], nodes[index + 1][0], "下一步") for index in range(len(nodes) - 1)]
            if len(nodes) > 1:
                edges.append((nodes[-1][0], nodes[0][0], "循环"))
        elif kind == "navigation":
            title = "信息架构与页面导航"
            surface_by_id = {item.id: item for item in report.surfaces}
            nodes = [
                (surface_id, surface_by_id[surface_id].title, surface_by_id[surface_id].kind)
                for surface_id in spec.information_architecture.surface_ids
            ]
            edges = [
                (item.from_surface_id, item.to_surface_id, item.trigger)
                for item in spec.information_architecture.edges
            ]
        elif kind == "interaction":
            title = "玩家操作与系统响应"
            steps = [step for item in spec.interaction_specs for step in item.steps]
            nodes = [
                (item.id, f"{item.order}. {item.actor}", f"{item.action} · {item.response or ''}")
                for item in steps
            ]
            edges = [(nodes[index][0], nodes[index + 1][0], "") for index in range(len(nodes) - 1)]
        else:
            raise ValueError(f"unknown design diagram: {kind}")
        return cls._graph_svg(title, nodes, edges)

    @classmethod
    def _graph_svg(
        cls,
        title: str,
        nodes: list[tuple[str, str, str]],
        edges: list[tuple[str, str, str]],
    ) -> str:
        width = 1200
        height = max(260, 130 + len(nodes) * 150)
        positions = {node_id: 110 + index * 150 for index, (node_id, _, _) in enumerate(nodes)}
        edge_svg = "".join(
            f'<path d="M 600 {positions[src] + 42} C 770 {positions[src] + 70}, 770 {positions[dst] - 70}, 600 {positions[dst] - 42}" class="edge" marker-end="url(#arrow)"/>'
            f'<text x="790" y="{(positions[src] + positions[dst]) / 2:.0f}" class="edge-label">{cls._e(label[:42])}</text>'
            for src, dst, label in edges
            if src in positions and dst in positions
        )
        node_svg = "".join(
            f'<g id="{cls._e(node_id)}" transform="translate(170 {positions[node_id] - 42})">'
            f'<rect width="860" height="84" rx="14"/><text x="24" y="32" class="node-title">{cls._e(label[:60])}</text>'
            f'<text x="24" y="60" class="node-detail">{cls._e(detail[:110])}</text></g>'
            for node_id, label, detail in nodes
        )
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{cls._e(title)}</title><desc id="desc">由结构化反推设计对象生成的可访问关系图，共 {len(nodes)} 个节点。</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>
<style>svg{{background:#fffdf7}}rect{{fill:#102b3b;stroke:#e45f36;stroke-width:2}}text{{font-family:Inter,"Noto Sans SC",sans-serif}}.node-title{{fill:#fff;font-size:22px;font-weight:700}}.node-detail{{fill:#bdd0d8;font-size:15px}}.edge{{fill:none;stroke:#e45f36;stroke-width:3}}.edge-label{{fill:#73412f;font-size:14px}}</style>
<text x="60" y="58" style="font:700 28px Inter,'Noto Sans SC',sans-serif;fill:#182936">{cls._e(title)}</text>{edge_svg}{node_svg}</svg>'''
