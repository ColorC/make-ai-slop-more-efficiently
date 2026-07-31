# [OMNI] origin=codex domain=slidecast ts=2026-07-24T00:00:00Z type=helper status=active
# [OMNI] summary="Slidecast theme sample book: one density baseline and four independent composition systems."
# [OMNI] why="Keep facts and page roles comparable while letting blueprint, CRT, comic, and notebook use their own spatial grammar."
# [OMNI] tags=slidecast,theme,blueprint,crt,comic,notebook
"""Comparable visual systems for the shared Field Manual Slidev component base.

The vendored ``slidev-theme-field-manual`` remains the component authority.
Facts and page roles stay comparable, while each direction owns its
composition, hierarchy, component geometry, and surface treatment.
"""

from __future__ import annotations

from collections.abc import Iterable


SUPPORTED_THEMES = ("blueprint", "crt", "comic", "notebook")
DEFAULT_THEME = "crt"

THEME_LABELS = {
    "blueprint": "蓝图",
    "crt": "CRT",
    "comic": "漫画",
    "notebook": "笔记",
}

THEME_DESCRIPTIONS = {
    "blueprint": "工程蓝晒 × CAD 制图：网格、尺寸线、编号与冷色高对比。",
    "crt": "终端设备 × 磷光显示：扫描、余辉、光标与轻微同步漂移。",
    "comic": "四色印刷 × 分镜漫画：粗墨线、网点、硬阴影与色块节奏。",
    "notebook": "编辑手帐 × 研究笔记：横线纸、索引卡、胶带与批注痕迹。",
}


COMMON_DENSITY_CSS = r"""
/* ===== shared 960×540 / 1080p readability and density contract ===== */
:root{
  --text-xs:.72rem;
  --text-sm:.88rem;
  --text-base:1.08rem;
  --text-md:1.24rem;
  --text-lg:1.55rem;
  --text-xl:2.08rem;
  --text-2xl:2.72rem;
  --text-3xl:3.72rem;
  --text-4xl:5.40rem;
  --space-1:.25rem;
  --space-2:.50rem;
  --space-3:.72rem;
  --space-4:1rem;
  --space-5:1.35rem;
  --space-6:1.85rem;
  --space-7:2.65rem;
  --space-8:3.50rem;
}
.slidev-layout,
.slidev-layout p,
.slidev-layout li,
.slidev-layout td,
.slidev-layout th{
  font-size:var(--text-base);
  line-height:1.44;
  text-wrap:pretty;
}
.slidev-layout h1,
.slidev-layout h2,
.slidev-layout h3{
  text-wrap:balance;
}
.cover-title{font-size:3.35rem !important;line-height:1.04 !important;max-width:760px;}
.cover-subtitle{font-size:1.18rem !important;line-height:1.42 !important;max-width:720px;}
.statement-content{max-width:780px !important;}
.statement-content h1,
.statement-content h2,
.statement-content p{font-size:2.28rem !important;line-height:1.2 !important;}
.layout-section h2{font-size:3.55rem !important;}
.end-title{font-size:3.05rem !important;}
.fm-header__inner{display:none !important;}
.fm-header__class{display:none !important;}
.fm-footer__section,.fm-footer__doc{visibility:hidden !important;}
.classification-banner{display:none !important;}
.db-panel-footer:empty{display:none;}
.db-panel-content{min-height:0;}
.db-panel-content .fm-stat{font-size:3.25rem !important;line-height:1 !important;}
.cmp-col-content,
.tc-col,
.layout-default__content{font-size:var(--text-base);}
.cmp-col-content,.tc-col{
  display:flex;
  flex-direction:column;
  justify-content:center;
}
.cmp-col-content li,
.tc-col li,
.layout-default__content li{margin-bottom:.52rem;}
.ca-body{
  display:grid !important;
  grid-template-columns:minmax(0,1.06fr) minmax(0,.94fr);
  align-items:center;
  gap:var(--space-5) !important;
}
.ca-content,.ca-main{flex:0 1 auto !important;overflow:visible !important;min-width:0;}
.ca-body>.fm-callout-box{margin:0 !important;align-self:center;}
.cf-chart-area>*,.cr-chart-area>*,.cl-chart-area>*{
  display:flex !important;
  align-items:center;
  justify-content:center;
}
.cf-chart-area svg,.cr-chart-area svg,.cl-chart-area svg{
  width:82% !important;
  max-height:92% !important;
  margin:auto !important;
}
.slidev-vclick-target{transition:opacity .22s ease,transform .22s ease;}
.slidev-vclick-hidden{transform:translateY(7px);}
@media (prefers-reduced-motion:reduce){
  .slidev-layout,
  .slidev-layout::before,
  .slidev-layout::after,
  .slidev-vclick-target{animation:none !important;transition:none !important;}
}
"""


BLUEPRINT_CSS = r"""
/* ===== BLUEPRINT / mature technical-drawing system ===== */
:root{
  --c-paper:#0a4770;--c-paper-dark:#083d61;--c-paper-deeper:#073653;--c-paper-shadow:#052b45;
  --c-ink:#f3fbff;--c-ink-muted:#d4efff;--c-ink-light:#9fd8f5;
  --c-olive-dark:#bfeaff;--c-olive:#d7f4ff;--c-olive-mid:#8dd6f5;--c-olive-light:#61bfeb;
  --c-olive-ghost:rgba(211,244,255,.08);--c-olive-subtle:rgba(211,244,255,.20);
  --c-khaki-dark:#69b5d8;--c-khaki:#8fd0eb;--c-khaki-light:#c9efff;--c-khaki-pale:#1a5b7d;
  --c-amber:#ffd15c;--c-amber-pale:rgba(255,209,92,.14);
  --c-red:#ff8f77;--c-red-light:#ffb09e;--c-red-pale:rgba(255,143,119,.16);
  --c-blue:#d7f4ff;--c-blue-mid:#aee7ff;--c-blue-light:#e9f9ff;--c-blue-pale:rgba(215,244,255,.13);
  --color-accent:#ffd15c;--color-accent-alt:#aee7ff;
  --color-rule:#d7f4ff;--color-rule-light:rgba(215,244,255,.48);
  --bracket-color:#d7f4ff;
  --font-heading:"DIN Alternate","Bahnschrift SemiCondensed","Noto Sans SC","Microsoft YaHei",sans-serif;
  --font-condensed-sans:"Bahnschrift Condensed","Arial Narrow","Noto Sans SC",sans-serif;
  --font-label:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
  --font-body:"Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
  --font-mono:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
}
.slidev-layout{
  color:#f3fbff !important;
  background-color:#0a4770 !important;
  background-image:
    linear-gradient(rgba(215,244,255,.085) 1px,transparent 1px),
    linear-gradient(90deg,rgba(215,244,255,.085) 1px,transparent 1px),
    linear-gradient(rgba(215,244,255,.035) 1px,transparent 1px),
    linear-gradient(90deg,rgba(215,244,255,.035) 1px,transparent 1px),
    radial-gradient(circle at 82% 18%,rgba(111,204,245,.16),transparent 34%) !important;
  background-size:40px 40px,40px 40px,8px 8px,8px 8px,auto !important;
  box-shadow:inset 0 0 0 2px rgba(215,244,255,.70),inset 0 0 0 8px rgba(215,244,255,.08);
}
.slidev-layout::before{
  opacity:.34 !important;
  mix-blend-mode:screen !important;
  background-image:
    repeating-linear-gradient(135deg,transparent 0 22px,rgba(255,255,255,.025) 22px 23px) !important;
}
.slidev-layout::after{
  content:"01  02  03  04  05  06  07  08  09  10";
  position:absolute;left:58px;right:58px;bottom:10px;
  color:rgba(215,244,255,.50);font:9px/1 "Cascadia Mono",monospace;
  letter-spacing:1.18em;white-space:nowrap;overflow:hidden;pointer-events:none;
}
.slidev-layout,
.slidev-layout p,
.slidev-layout li,
.slidev-layout td,
.slidev-layout th{font-family:var(--font-body);color:#f3fbff;}
.slidev-layout h1,.slidev-layout h2,.slidev-layout h3,.fm-stat{
  color:#fff !important;text-shadow:0 1px 0 rgba(0,0,0,.22);
}
.layout-default__rule-top,.tc-rule,.cmp-title-rule,.tl-rule,.db-rule{
  background:#d7f4ff !important;
  box-shadow:0 4px 0 rgba(215,244,255,.12);
}
.cover-frame,.db-panel,.cmp-col-header,.fm-callout{
  border-color:rgba(215,244,255,.78) !important;
}
.cover-frame{background:rgba(5,45,72,.30) !important;}
.cover-rule-top,.cover-rule-mid,.section-top-rule,.statement-rule-top,.statement-rule-bottom{
  background:#ffd15c !important;
}
.db-panel{background:rgba(5,45,72,.48) !important;}
.db-panel-header{
  background:rgba(215,244,255,.10) !important;
  border-bottom:1px solid rgba(215,244,255,.55) !important;
}
.db-panel-label{color:#f3fbff !important;}
.db-panel-content{
  background-image:
    linear-gradient(rgba(215,244,255,.07) 1px,transparent 1px),
    linear-gradient(90deg,rgba(215,244,255,.07) 1px,transparent 1px) !important;
  background-size:16px 16px !important;
}
.fm-stat{color:#ffd15c !important;}
.tl-entry-dot{border-color:#ffd15c !important;background:#0a4770 !important;}
.tl-entry-dot::after{background:#ffd15c !important;}
.tl-track::before{background:#d7f4ff !important;}
.cmp-divider-vs{color:#ffd15c !important;}
.slidev-layout svg .node rect,.slidev-layout svg .node polygon,.slidev-layout svg .node circle{
  fill:#0a4770 !important;stroke:#d7f4ff !important;stroke-width:2px !important;
}
.slidev-layout svg .nodeLabel,.slidev-layout svg .nodeLabel p,.slidev-layout svg .label text{
  color:#f3fbff !important;fill:#f3fbff !important;
}
.slidev-layout svg .flowchart-link,.slidev-layout svg .edgePath path{stroke:#ffd15c !important;stroke-width:2px !important;}
.slidev-layout svg marker path{fill:#ffd15c !important;stroke:#ffd15c !important;}
.slidev-layout :not(pre)>code{
  color:#ffd15c !important;background:rgba(4,38,61,.62) !important;border:1px solid rgba(215,244,255,.42);
}
"""


CRT_CSS = r"""
/* ===== CRT / mature phosphor-terminal system ===== */
@font-face{font-family:"FusionPixelLatin";src:url("/fonts/fusion-pixel-latin.woff2") format("woff2");font-display:swap;}
@font-face{font-family:"FusionPixelZH";src:url("/fonts/fusion-pixel-zh.woff2") format("woff2");font-display:swap;}
:root{
  --c-paper:#020503;--c-paper-dark:#061009;--c-paper-deeper:#07130d;--c-paper-shadow:#0a1810;
  --c-ink:#d7ffe8;--c-ink-muted:#9fe0bf;--c-ink-light:#75b98f;
  --c-olive-dark:#15cc70;--c-olive:#1aff8c;--c-olive-mid:#36ffa0;--c-olive-light:#5effb0;
  --c-olive-ghost:rgba(26,255,140,.08);--c-olive-subtle:rgba(26,255,140,.16);
  --c-khaki-dark:#3a7a60;--c-khaki:#4ea882;--c-khaki-light:#6abf95;--c-khaki-pale:#2a4a3a;
  --c-amber:#ffd166;--c-amber-pale:rgba(255,209,102,.12);
  --c-red:#ff5d73;--c-red-light:#ff7d8d;--c-red-pale:rgba(255,93,115,.14);
  --c-blue:#3a6da8;--c-blue-mid:#4a7db8;--c-blue-light:#5a8dc8;--c-blue-pale:rgba(58,109,168,.16);
  --color-accent:#1aff8c;--color-accent-alt:#3a6da8;
  --color-rule:#1aff8c;--color-rule-light:rgba(26,255,140,.55);--bracket-color:#1aff8c;
  --font-heading:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-condensed-sans:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-label:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-body:"Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
  --font-mono:"Cascadia Code","FusionPixelLatin","Microsoft YaHei",monospace;
}
.slidev-layout{
  color:#d7ffe8 !important;background-color:#020503 !important;
  background-image:
    repeating-linear-gradient(0deg,rgba(0,0,0,.18) 0 1px,transparent 1px 3px),
    linear-gradient(rgba(26,255,140,.035) 1px,transparent 1px),
    linear-gradient(90deg,rgba(26,255,140,.035) 1px,transparent 1px),
    radial-gradient(ellipse at 50% 112%,rgba(26,255,140,.11),transparent 55%),
    radial-gradient(ellipse at 50% -10%,rgba(26,255,140,.13),transparent 48%) !important;
  background-size:auto,44px 44px,44px 44px,auto,auto !important;
  box-shadow:inset 0 0 155px rgba(0,0,0,.58),inset 0 0 60px rgba(26,255,140,.045);
  animation:crt-power-on .38s cubic-bezier(.2,.8,.2,1) both;
}
.slidev-layout::before{
  opacity:.18 !important;mix-blend-mode:screen !important;
  background-image:repeating-linear-gradient(90deg,rgba(255,0,0,.08) 0 1px,rgba(0,255,0,.04) 1px 2px,rgba(0,80,255,.08) 2px 3px) !important;
  background-size:3px 100% !important;
}
.slidev-layout::after{
  content:"";position:absolute;inset:-20% 0 auto;height:18%;
  pointer-events:none;z-index:1001;opacity:.12;
  background:linear-gradient(180deg,transparent,rgba(110,255,180,.48),transparent);
  animation:crt-scan 7s linear infinite;
}
@keyframes crt-power-on{
  0%{filter:brightness(2.2) contrast(.35);transform:scaleY(.03) scaleX(.78);}
  45%{filter:brightness(1.45) contrast(.8);transform:scaleY(1) scaleX(.99);}
  100%{filter:none;transform:none;}
}
@keyframes crt-scan{to{transform:translateY(760px);}}
@keyframes crt-cursor{0%,48%{opacity:1}49%,100%{opacity:0}}
.slidev-layout,
.slidev-layout p,
.slidev-layout li,
.slidev-layout td,
.slidev-layout th{font-family:var(--font-body);color:#d7ffe8;}
.slidev-layout h1,.slidev-layout h2,.slidev-layout h3,.fm-label,.fm-stat{
  -webkit-font-smoothing:none;image-rendering:pixelated;
  text-shadow:0 0 4px rgba(26,255,140,.5),0 0 14px rgba(26,255,140,.22);
}
.layout-cover .cover-title::after{content:"_";margin-left:.12em;color:#1aff8c;animation:crt-cursor 1.05s steps(1,end) infinite;}
.fm-stat{color:#1aff8c !important;text-shadow:0 0 6px rgba(26,255,140,.58),0 0 18px rgba(26,255,140,.28) !important;}
.cover-frame,.db-panel{background:rgba(2,8,5,.62) !important;border-color:rgba(26,255,140,.45) !important;}
.cover-rule-top,.cover-rule-mid,.layout-default__rule-top,.tc-rule,.cmp-title-rule,.tl-rule,.db-rule{
  background:#1aff8c !important;box-shadow:0 0 10px rgba(26,255,140,.45);
}
.db-panel-header{
  background:#07130d !important;border-bottom-color:rgba(26,255,140,.35) !important;
}
.db-panel-label{color:#1aff8c !important;}
.db-panel-content{
  background-image:
    linear-gradient(rgba(26,255,140,.055) 1px,transparent 1px),
    linear-gradient(90deg,rgba(26,255,140,.055) 1px,transparent 1px) !important;
}
.slidev-layout svg .node rect,.slidev-layout svg .node polygon,.slidev-layout svg .node circle{
  fill:#061009 !important;stroke:#1aff8c !important;stroke-width:2px !important;
  filter:drop-shadow(0 0 4px rgba(26,255,140,.28));
}
.slidev-layout svg .nodeLabel,.slidev-layout svg .nodeLabel p,.slidev-layout svg .label text{
  color:#d7ffe8 !important;fill:#d7ffe8 !important;
}
.slidev-layout svg .flowchart-link,.slidev-layout svg .edgePath path{stroke:#1aff8c !important;stroke-width:2px !important;}
.slidev-layout svg marker path{fill:#1aff8c !important;stroke:#1aff8c !important;}
.slidev-layout pre,.slidev-layout .shiki{
  background:#020805 !important;border-color:rgba(26,255,140,.30) !important;
}
.slidev-layout pre code,.slidev-layout .shiki code,.slidev-layout .shiki span{color:#cdeede !important;}
.slidev-layout :not(pre)>code{
  color:#ffd166 !important;background:#061009 !important;border:1px solid rgba(26,255,140,.28);
}
.slidev-layout a{color:#ffd166 !important;}
.slidev-vclick-hidden{transform:translateX(-8px);filter:blur(2px);}
"""


COMIC_CSS = r"""
/* ===== COMIC / mature four-colour print and panel system ===== */
:root{
  --c-paper:#fff7df;--c-paper-dark:#fff0be;--c-paper-deeper:#ffe38a;--c-paper-shadow:#181818;
  --c-ink:#151515;--c-ink-muted:#262626;--c-ink-light:#565656;
  --c-olive-dark:#111;--c-olive:#ffd83d;--c-olive-mid:#ffef79;--c-olive-light:#fff3a8;
  --c-olive-ghost:rgba(255,216,61,.14);--c-olive-subtle:rgba(17,17,17,.14);
  --c-khaki-dark:#111;--c-khaki:#ffd83d;--c-khaki-light:#fff3a8;--c-khaki-pale:#fff0be;
  --c-amber:#ff8a00;--c-amber-pale:rgba(255,138,0,.14);
  --c-red:#f0344b;--c-red-light:#ff6476;--c-red-pale:rgba(240,52,75,.14);
  --c-blue:#168ce3;--c-blue-mid:#33a9ff;--c-blue-light:#89d4ff;--c-blue-pale:rgba(22,140,227,.14);
  --color-accent:#f0344b;--color-accent-alt:#168ce3;
  --color-rule:#151515;--color-rule-light:#151515;--bracket-color:#151515;
  --font-heading:"Arial Black","Noto Sans SC","Microsoft YaHei",sans-serif;
  --font-condensed-sans:"Arial Black","Noto Sans SC","Microsoft YaHei",sans-serif;
  --font-label:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
  --font-body:"Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
  --font-mono:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
  --rule-thick:4px;--rule-mid:3px;--rule-thin:2px;
}
.slidev-layout{
  color:#151515 !important;background-color:#fff7df !important;
  background-image:
    radial-gradient(circle at 1px 1px,rgba(21,21,21,.13) 1px,transparent 1.35px),
    linear-gradient(135deg,rgba(255,216,61,.11),transparent 36%) !important;
  background-size:9px 9px,auto !important;
  box-shadow:inset 0 0 0 4px #151515,inset 0 0 0 9px #fff7df,inset 0 0 0 11px #151515;
}
.slidev-layout::before{
  opacity:.08 !important;mix-blend-mode:multiply !important;
  background-image:radial-gradient(circle at 1px 1px,#151515 1px,transparent 1.2px) !important;
  background-size:5px 5px !important;
}
.slidev-layout,
.slidev-layout p,
.slidev-layout li,
.slidev-layout td,
.slidev-layout th{font-family:var(--font-body);color:#151515;}
.slidev-layout h1,.slidev-layout h2,.slidev-layout h3,.fm-stat{
  color:#151515 !important;letter-spacing:-.025em;
  text-shadow:2px 2px 0 #fff7df;
}
.layout-cover .cover-frame{
  background:#fffdf5 !important;border:4px solid #151515 !important;
  box-shadow:9px 9px 0 #151515;transform:rotate(-.35deg);
}
.cover-rule-top{background:#f0344b !important;height:8px !important;}
.cover-rule-mid{background:#168ce3 !important;height:5px !important;}
.layout-default__rule-top,.tc-rule,.cmp-title-rule,.tl-rule,.db-rule{
  background:linear-gradient(90deg,#f0344b 0 34%,#ffd83d 34% 67%,#168ce3 67%) !important;
  height:7px !important;border:2px solid #151515;
}
.layout-default__title,.tc-title,.cmp-title,.tl-title,.db-title{
  display:inline-block;background:#ffd83d;padding:.12em .32em .16em;
  border:3px solid #151515;box-shadow:4px 4px 0 #151515;
}
.db-panel{
  border:3px solid #151515 !important;background:#fffdf5 !important;
  box-shadow:5px 5px 0 #151515;
}
.db-panel:nth-child(2n){transform:rotate(.35deg);}
.db-panel:nth-child(2n+1){transform:rotate(-.25deg);}
.db-panel:nth-child(1) .db-panel-header{background:#ffd83d !important;}
.db-panel:nth-child(2) .db-panel-header{background:#79d6ff !important;}
.db-panel:nth-child(3) .db-panel-header{background:#ff8fa0 !important;}
.db-panel:nth-child(4) .db-panel-header{background:#a9e98e !important;}
.db-panel-header{border-bottom:3px solid #151515 !important;}
.db-panel-label{color:#151515 !important;}
.db-panel-content{background-image:none !important;}
.fm-stat{color:#f0344b !important;text-shadow:2px 2px 0 #ffd83d,4px 4px 0 #151515 !important;}
.cmp-col-header{
  border:3px solid #151515 !important;background:#fffdf5 !important;
  box-shadow:4px 4px 0 #151515;
}
.cmp-col-header--red{background:#ff8fa0 !important;}
.cmp-col-header--blue{background:#79d6ff !important;}
.cmp-col-header--olive{background:#ffd83d !important;}
.cmp-divider-rule{background:#151515 !important;}
.cmp-divider-vs{
  color:#fff !important;background:#f0344b;border:3px solid #151515;
  padding:.28em;transform:rotate(-8deg);box-shadow:3px 3px 0 #151515;
}
.statement-content{
  background:#fffdf5;border:4px solid #151515;border-radius:48%;
  padding:2rem 3rem;box-shadow:8px 8px 0 #151515;position:relative;
}
.statement-content::after{
  content:"";position:absolute;right:14%;bottom:-28px;width:44px;height:44px;
  background:#fffdf5;border-right:4px solid #151515;border-bottom:4px solid #151515;
  transform:skew(28deg) rotate(32deg);
}
.tl-entry-dot{border:3px solid #151515 !important;background:#ffd83d !important;}
.tl-entry-dot::after{background:#f0344b !important;}
.slidev-layout svg .node rect,.slidev-layout svg .node polygon,.slidev-layout svg .node circle{
  fill:#ffd83d !important;stroke:#151515 !important;stroke-width:3px !important;
  filter:drop-shadow(4px 4px 0 #151515);
}
.slidev-layout svg .node:nth-child(2n) rect{fill:#79d6ff !important;}
.slidev-layout svg .node:nth-child(3n) rect{fill:#ff8fa0 !important;}
.slidev-layout svg .nodeLabel,.slidev-layout svg .nodeLabel p,.slidev-layout svg .label text{
  color:#151515 !important;fill:#151515 !important;
}
.slidev-layout svg .flowchart-link,.slidev-layout svg .edgePath path{stroke:#151515 !important;stroke-width:3px !important;}
.slidev-layout svg marker path{fill:#151515 !important;stroke:#151515 !important;}
.slidev-layout ul li::before{content:"★" !important;color:#f0344b !important;left:-1.7em !important;}
.slidev-layout :not(pre)>code{
  color:#151515 !important;background:#ffd83d !important;border:2px solid #151515;
  box-shadow:2px 2px 0 #151515;
}
.slidev-vclick-hidden{transform:scale(.88) rotate(-1.5deg);}
"""


NOTEBOOK_CSS = r"""
/* ===== NOTEBOOK / mature editorial-note and index-card system ===== */
:root{
  --c-paper:#fffdf4;--c-paper-dark:#fffaf0;--c-paper-deeper:#f3ead5;--c-paper-shadow:#cfc4ab;
  --c-ink:#242a31;--c-ink-muted:#3f4b58;--c-ink-light:#747d86;
  --c-olive-dark:#405d4b;--c-olive:#5e7d68;--c-olive-mid:#7c9a82;--c-olive-light:#b7c9b9;
  --c-olive-ghost:rgba(94,125,104,.08);--c-olive-subtle:rgba(94,125,104,.18);
  --c-khaki-dark:#a68d5b;--c-khaki:#c5ad77;--c-khaki-light:#dec99a;--c-khaki-pale:#eee1c2;
  --c-amber:#d68827;--c-amber-pale:rgba(214,136,39,.13);
  --c-red:#d95b5b;--c-red-light:#ed7d7d;--c-red-pale:rgba(217,91,91,.12);
  --c-blue:#477eb4;--c-blue-mid:#6098cc;--c-blue-light:#90b9dd;--c-blue-pale:rgba(71,126,180,.12);
  --color-accent:#d95b5b;--color-accent-alt:#477eb4;
  --color-rule:#477eb4;--color-rule-light:rgba(71,126,180,.36);--bracket-color:#d95b5b;
  --font-heading:"Noto Serif SC","Songti SC","SimSun",serif;
  --font-condensed-sans:"Noto Sans SC","Microsoft YaHei",sans-serif;
  --font-label:"KaiTi","STKaiti","Microsoft YaHei",sans-serif;
  --font-body:"Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
  --font-mono:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
}
.slidev-layout{
  color:#242a31 !important;background-color:#fffdf4 !important;
  background-image:
    linear-gradient(90deg,transparent 0 62px,rgba(217,91,91,.34) 62px 64px,transparent 64px),
    repeating-linear-gradient(0deg,transparent 0 27px,rgba(71,126,180,.19) 27px 28px),
    radial-gradient(circle at 88% 12%,rgba(255,220,95,.16),transparent 26%) !important;
  box-shadow:inset 0 0 35px rgba(90,74,42,.08);
}
.slidev-layout::before{
  opacity:.16 !important;mix-blend-mode:multiply !important;
}
.slidev-layout::after{
  content:"";position:absolute;left:17px;top:28px;bottom:28px;width:19px;
  pointer-events:none;opacity:.34;
  background:radial-gradient(circle,#8b8170 0 4px,#f4ecdc 4.5px 7px,transparent 7.5px) 0 0/19px 58px repeat-y;
}
.slidev-layout,
.slidev-layout p,
.slidev-layout li,
.slidev-layout td,
.slidev-layout th{font-family:var(--font-body);color:#242a31;}
.slidev-layout h1,.slidev-layout h2,.slidev-layout h3{
  color:#242a31 !important;text-shadow:none;
}
.layout-default__rule-top,.tc-rule,.cmp-title-rule,.tl-rule,.db-rule{
  height:4px !important;background:#477eb4 !important;border-radius:70% 30% 60% 40%;
  transform:rotate(-.18deg);
}
.layout-default__title,.tc-title,.cmp-title,.tl-title,.db-title{
  display:inline;padding:0 .12em;
  background:linear-gradient(transparent 58%,rgba(255,220,95,.60) 58% 92%,transparent 92%);
}
.layout-cover .cover-frame{
  background:rgba(255,253,244,.91) !important;border:1px solid rgba(77,72,61,.28) !important;
  box-shadow:0 12px 30px rgba(66,52,32,.14);transform:rotate(-.22deg);
}
.cover-rule-top{background:#d95b5b !important;}
.cover-rule-mid{background:#477eb4 !important;}
.cover-doc-number,.cover-date{font-family:var(--font-label) !important;text-transform:none !important;}
.db-panel{
  position:relative;background:#fffef9 !important;border:1px solid #cfc4ab !important;
  box-shadow:2px 4px 9px rgba(67,54,33,.13);overflow:visible !important;
}
.db-panel::before{
  content:"";position:absolute;z-index:3;top:-7px;left:50%;width:70px;height:17px;
  transform:translateX(-50%) rotate(-1.2deg);
  background:rgba(234,215,169,.72);border-left:1px dashed rgba(109,91,56,.22);border-right:1px dashed rgba(109,91,56,.22);
}
.db-panel:nth-child(even)::before{transform:translateX(-50%) rotate(1.4deg);}
.db-panel-header{background:#f3ead5 !important;border-bottom:1px dashed #a68d5b !important;}
.db-panel-label{color:#405d4b !important;text-transform:none !important;}
.db-panel-content{background-image:none !important;}
.fm-stat{color:#477eb4 !important;text-shadow:none !important;}
.cmp-col-header{
  background:#fffef9 !important;border:1px solid #cfc4ab !important;
  box-shadow:2px 3px 7px rgba(67,54,33,.12);
}
.cmp-col-header--red{border-left:7px solid #d95b5b !important;}
.cmp-col-header--blue{border-left:7px solid #477eb4 !important;}
.cmp-col-header--olive{border-left:7px solid #5e7d68 !important;}
.cmp-divider-vs{font-family:var(--font-label);color:#d95b5b !important;transform:rotate(-6deg);}
.statement-content{
  padding:1.25rem 1.8rem;background:rgba(255,253,244,.82);
  border:1px solid rgba(166,141,91,.38);box-shadow:3px 5px 12px rgba(67,54,33,.10);
  transform:rotate(-.35deg);
}
.statement-content::before{
  content:"※";position:absolute;left:-2rem;top:-1.2rem;color:#d95b5b;
  font:2rem/1 var(--font-label);transform:rotate(-12deg);
}
.slidev-layout ul li::before{content:"□" !important;color:#477eb4 !important;left:-1.65em !important;}
.tl-entry-dot{border-color:#477eb4 !important;background:#fffdf4 !important;}
.tl-entry-dot::after{background:#d95b5b !important;}
.slidev-layout svg .node rect,.slidev-layout svg .node polygon,.slidev-layout svg .node circle{
  fill:#fffef9 !important;stroke:#477eb4 !important;stroke-width:2px !important;
  filter:drop-shadow(2px 3px 2px rgba(67,54,33,.14));
}
.slidev-layout svg .nodeLabel,.slidev-layout svg .nodeLabel p,.slidev-layout svg .label text{
  color:#242a31 !important;fill:#242a31 !important;
}
.slidev-layout svg .flowchart-link,.slidev-layout svg .edgePath path{stroke:#477eb4 !important;stroke-width:2px !important;}
.slidev-layout svg marker path{fill:#477eb4 !important;stroke:#477eb4 !important;}
.slidev-layout :not(pre)>code{
  color:#405d4b !important;background:rgba(255,220,95,.32) !important;
  border:0;border-bottom:2px solid rgba(214,136,39,.55);
}
.slidev-vclick-hidden{transform:translateY(5px) rotate(-.35deg);}
"""

V2_SHARED_LAYOUT_CSS = r"""
/* ===== v2 composition contract: same facts, different spatial grammar ===== */
.fm-ascii{display:none !important;}
.layout-cover .cover-body,
.layout-cover .cover-frame,
.layout-end .end-body,
.layout-end .end-text{box-sizing:border-box;}
"""


BLUEPRINT_LAYOUT_CSS = r"""
/* Blueprint: asymmetric drawing sheet + modular engineering rails. */
.layout-cover .cover-body{
  align-items:flex-start !important;justify-content:flex-start !important;
  padding:58px 70px 46px !important;
}
.layout-cover .cover-frame{
  width:68% !important;max-width:none !important;margin:44px 0 0 !important;
  padding:30px 34px 34px !important;border:0 !important;
  border-left:5px solid #d7f4ff !important;border-top:1px solid #d7f4ff !important;
  background:rgba(5,45,72,.28) !important;box-shadow:none !important;
}
.layout-cover .cover-frame::before{
  content:"SHEET  A-01\A SCALE  1:1\A GRID   08×08\A REV.   02";
  white-space:pre;position:absolute;left:calc(100% + 46px);top:0;width:190px;
  padding:18px;border:1px solid rgba(215,244,255,.72);
  color:#d7f4ff;font:600 .72rem/1.85 "Cascadia Mono",monospace;letter-spacing:.12em;
  background:rgba(5,45,72,.44);
}
.layout-cover .cover-frame::after{
  content:"";position:absolute;left:calc(100% + 46px);top:165px;width:190px;height:116px;
  border:1px solid rgba(215,244,255,.52);
  background:
    linear-gradient(30deg,transparent 48%,rgba(255,209,92,.8) 49% 51%,transparent 52%),
    linear-gradient(150deg,transparent 48%,rgba(255,209,92,.8) 49% 51%,transparent 52%),
    radial-gradient(circle at 50% 50%,transparent 0 31px,#d7f4ff 32px 33px,transparent 34px);
}
.layout-cover .cover-doc-number{color:#ffd15c !important;font-size:.75rem !important;}
.layout-cover .cover-title{
  font-size:4rem !important;line-height:.98 !important;letter-spacing:-.035em !important;
  text-align:left !important;margin-bottom:20px !important;
}
.layout-cover .cover-subtitle{
  max-width:610px !important;font-size:1.16rem !important;line-height:1.45 !important;
}
.layout-cover .cover-rule-top{height:2px !important;margin-bottom:26px !important;}
.layout-cover .cover-rule-mid{width:72% !important;height:2px !important;}

.layout-timeline .tl-track--horizontal{
  display:grid !important;grid-template-columns:repeat(4,minmax(0,1fr)) !important;
  align-items:stretch !important;gap:12px !important;padding-top:26px !important;overflow:visible !important;
}
.layout-timeline .tl-track--horizontal::before{
  top:12px !important;height:1px !important;background:#ffd15c !important;
}
.layout-timeline .tl-track--horizontal .tl-entry{
  min-width:0 !important;align-items:stretch !important;
  border:1px solid rgba(215,244,255,.72);background:rgba(5,45,72,.5);
}
.layout-timeline .tl-entry-marker{position:absolute !important;top:-21px !important;left:16px !important;}
.layout-timeline .tl-entry-body{text-align:left !important;padding:14px 14px 16px !important;}
.layout-timeline .tl-entry-date{color:#ffd15c !important;margin-bottom:12px !important;}
.layout-timeline .tl-entry-title{font-size:1.16rem !important;min-height:2.7em;}
.layout-timeline .tl-entry-desc{font-size:.86rem !important;line-height:1.45 !important;}

.layout-chart-full .cf-chart-area{
  justify-content:flex-start !important;padding:28px 184px 28px 34px !important;
}
.layout-chart-full .cf-chart-area::after{
  content:"DEPENDENCY MAP\A\A INPUT   ARTICLE\A CORE    SCRIPT\A OUTPUT  3 LANES";
  white-space:pre;position:absolute;right:18px;top:18px;bottom:18px;width:142px;
  padding:16px 13px;border:1px solid rgba(215,244,255,.65);
  color:#d7f4ff;background:rgba(5,45,72,.55);
  font:600 .68rem/1.75 "Cascadia Mono",monospace;letter-spacing:.07em;
}
.layout-chart-full .cf-chart-area svg{width:100% !important;}

.layout-dashboard .db-grid--4{
  grid-template-columns:repeat(4,minmax(0,1fr)) !important;grid-template-rows:1fr !important;
  gap:10px !important;
}
.layout-dashboard .db-panel{border-top:5px solid #ffd15c !important;}
.layout-dashboard .db-panel-content .fm-stat{font-size:2.05rem !important;line-height:1.05 !important;}
.layout-dashboard .db-panel-footer{min-height:66px;padding:9px 11px !important;line-height:1.35 !important;}
.layout-section .section-content{
  max-width:78% !important;border-left:8px solid #ffd15c;padding-left:28px;
}
.layout-end .end-body{justify-content:flex-start !important;padding:66px 74px !important;}
.layout-end .end-text{max-width:650px !important;margin-left:0 !important;}
.layout-end .end-text::after{
  content:"DRAWING CLOSED\A CHECKED / 24.07";
  white-space:pre;position:absolute;right:78px;bottom:72px;width:210px;padding:16px;
  border:2px solid #ffd15c;color:#ffd15c;font:700 .72rem/1.7 "Cascadia Mono",monospace;
  transform:rotate(-2deg);
}
"""


CRT_LAYOUT_CSS = r"""
/* CRT: boot console, vertical event log and live status rows. */
.layout-cover .cover-body{
  align-items:flex-start !important;justify-content:flex-start !important;
  padding:72px 76px 48px !important;
}
.layout-cover .cover-frame{
  width:82% !important;max-width:none !important;margin:86px 0 0 !important;
  padding:0 0 26px !important;border:0 !important;background:transparent !important;
  box-shadow:none !important;
}
.layout-cover .cover-frame::before{
  content:"> BOOT /CHANNEL/INTRO\A> LOAD STORY_PIPELINE ........ OK\A> SYNC AUDIO_CLOCK .......... OK";
  white-space:pre;position:absolute;left:0;top:-96px;color:#75b98f;
  font:500 .72rem/1.72 "Cascadia Code",monospace;letter-spacing:.045em;
}
.layout-cover .cover-frame::after{
  content:"READY  █";position:absolute;right:0;bottom:-6px;color:#1aff8c;
  font:700 .8rem/1 "Cascadia Code",monospace;letter-spacing:.14em;
  animation:crt-cursor 1.05s steps(1,end) infinite;
}
.layout-cover .cover-doc-number{color:#ffd166 !important;margin-bottom:13px !important;}
.layout-cover .cover-rule-top{height:1px !important;margin-bottom:20px !important;}
.layout-cover .cover-title{
  font-size:4.45rem !important;line-height:1 !important;letter-spacing:.01em !important;
  text-align:left !important;margin-bottom:18px !important;
}
.layout-cover .cover-title::after{content:"" !important;}
.layout-cover .cover-subtitle{
  width:76% !important;max-width:none !important;color:#9fe0bf !important;
  font-family:"Cascadia Code","Microsoft YaHei",monospace !important;
}
.layout-cover .cover-rule-mid{width:76% !important;height:1px !important;}

.layout-timeline .tl-track--horizontal{
  display:grid !important;grid-template-columns:1fr !important;
  gap:7px !important;padding:4px 0 !important;margin:0 !important;overflow:visible !important;
}
.layout-timeline .tl-track--horizontal::before{display:none !important;}
.layout-timeline .tl-track--horizontal .tl-entry{
  display:block !important;min-width:0 !important;border:1px solid rgba(26,255,140,.28);
  background:rgba(2,8,5,.72);
}
.layout-timeline .tl-entry-marker{display:none !important;}
.layout-timeline .tl-entry-body{
  display:grid !important;grid-template-columns:92px 190px 1fr !important;
  align-items:center !important;text-align:left !important;padding:9px 13px !important;gap:14px;
}
.layout-timeline .tl-entry-date{color:#ffd166 !important;}
.layout-timeline .tl-entry-title{font-size:1.04rem !important;color:#1aff8c !important;}
.layout-timeline .tl-entry-desc{font:.78rem/1.35 "Cascadia Code","Microsoft YaHei",monospace !important;color:#9fe0bf !important;}

.layout-chart-full .cf-chart-area{
  align-items:center !important;padding:54px 28px 22px !important;
  border:1px solid #1aff8c !important;background-color:rgba(1,12,7,.82) !important;
}
.layout-chart-full .cf-chart-area::before{
  content:"[ PIPELINE_MAP.EXE ]     MODE=TRACE     STATUS=LIVE";
  position:absolute;left:0;right:0;top:0;height:34px;display:flex;align-items:center;
  padding:0 14px;border-bottom:1px solid rgba(26,255,140,.42);
  color:#75b98f;background:rgba(4,20,12,.92);
  font:600 .68rem/1 "Cascadia Code",monospace;letter-spacing:.08em;
}
.layout-chart-full .cf-chart-area svg{width:88% !important;}

.layout-dashboard .db-grid--4{
  grid-template-columns:1fr !important;grid-template-rows:repeat(4,minmax(0,1fr)) !important;
  gap:7px !important;
}
.layout-dashboard .db-panel{
  display:grid !important;grid-template-columns:170px 235px 1fr !important;
  align-items:stretch !important;min-height:0 !important;
}
.layout-dashboard .db-panel-header{
  display:flex;align-items:center;border-right:1px solid rgba(26,255,140,.3) !important;
  border-bottom:0 !important;padding:8px 13px !important;
}
.layout-dashboard .db-panel-content{padding:5px 12px !important;}
.layout-dashboard .db-panel-content .fm-stat{font-size:1.8rem !important;}
.layout-dashboard .db-panel-footer{
  display:flex;align-items:center;border-left:1px solid rgba(26,255,140,.2);
  padding:7px 12px !important;font:.76rem/1.3 "Cascadia Code","Microsoft YaHei",monospace;
}
.layout-section .section-body{align-items:flex-start !important;}
.layout-section .section-content{max-width:86% !important;}
.layout-section .section-content::before{content:"> RUN ";color:#1aff8c;font-family:"Cascadia Code",monospace;}
.layout-callout .fm-callout-box{border-style:solid !important;border-width:1px !important;}
.layout-end .end-body{justify-content:flex-start !important;padding:72px 82px !important;}
.layout-end .end-text{max-width:720px !important;}
.layout-end .end-text::before{
  content:"> SESSION SUMMARY\A> ALL FRAMES COMMITTED\A> EXIT CODE: 0";
  white-space:pre;display:block;margin-bottom:28px;color:#75b98f;
  font:500 .76rem/1.7 "Cascadia Code",monospace;
}
"""


COMIC_LAYOUT_CSS = r"""
/* Comic: splash cover, 2×2 sequential panels and exaggerated editorial beats. */
.layout-cover{
  background-image:
    radial-gradient(circle at 78% 22%,#ff8fa0 0 10%,transparent 10.5%),
    repeating-conic-gradient(from -12deg at 78% 22%,rgba(240,52,75,.22) 0 7deg,transparent 7deg 14deg),
    radial-gradient(circle at 1px 1px,rgba(21,21,21,.13) 1px,transparent 1.35px) !important;
  background-size:auto,auto,9px 9px !important;
}
.layout-cover .cover-body{
  align-items:flex-end !important;justify-content:flex-start !important;
  padding:54px 62px 62px !important;
}
.layout-cover .cover-frame{
  width:78% !important;max-width:none !important;margin:0 !important;
  padding:32px 38px 34px !important;transform:rotate(-1.2deg) !important;
  border:5px solid #151515 !important;box-shadow:13px 13px 0 #151515 !important;
}
.layout-cover .cover-frame::before{
  content:"ISSUE 01";position:absolute;right:-112px;top:-88px;
  width:150px;height:150px;display:grid;place-items:center;border:5px solid #151515;
  border-radius:50%;background:#ffd83d;color:#151515;
  font:900 1.2rem/1 "Arial Black","Noto Sans SC",sans-serif;transform:rotate(11deg);
  box-shadow:7px 7px 0 #151515;
}
.layout-cover .cover-doc-number{
  display:inline-block;color:#fff !important;background:#f0344b;padding:5px 10px;
  border:3px solid #151515;transform:rotate(-2deg);
}
.layout-cover .cover-title{
  font-size:4.6rem !important;line-height:.93 !important;letter-spacing:-.06em !important;
  text-align:left !important;margin:18px 0 20px !important;
}
.layout-cover .cover-subtitle{
  display:inline-block;max-width:670px !important;padding:9px 13px;
  background:#fff;border:3px solid #151515;font-weight:700;
}
.layout-cover .cover-rule-top,.layout-cover .cover-rule-mid{display:none !important;}

.layout-timeline .tl-track--horizontal{
  display:grid !important;grid-template-columns:repeat(2,minmax(0,1fr)) !important;
  grid-template-rows:repeat(2,minmax(0,1fr)) !important;
  gap:14px !important;padding:2px !important;margin:0 !important;overflow:visible !important;
}
.layout-timeline .tl-track--horizontal::before{display:none !important;}
.layout-timeline .tl-track--horizontal .tl-entry{
  display:block !important;min-width:0 !important;align-items:stretch !important;
  border:4px solid #151515;background:#fffdf5;box-shadow:5px 5px 0 #151515;
}
.layout-timeline .tl-track--horizontal .tl-entry:nth-child(1){background:#fff6bd;}
.layout-timeline .tl-track--horizontal .tl-entry:nth-child(2){background:#d7f1ff;transform:rotate(.4deg);}
.layout-timeline .tl-track--horizontal .tl-entry:nth-child(3){background:#ffdce2;transform:rotate(-.35deg);}
.layout-timeline .tl-track--horizontal .tl-entry:nth-child(4){background:#e0f8d6;}
.layout-timeline .tl-entry-marker{display:none !important;}
.layout-timeline .tl-entry-body{text-align:left !important;padding:12px 15px !important;}
.layout-timeline .tl-entry-date{
  display:inline-block;background:#151515;color:#fff !important;padding:2px 7px;margin-bottom:6px;
}
.layout-timeline .tl-entry-title{font:900 1.18rem/1.1 "Arial Black","Noto Sans SC",sans-serif !important;}
.layout-timeline .tl-entry-desc{font-size:.84rem !important;line-height:1.35 !important;margin-top:5px;}

.layout-chart-full .cf-chart-area{
  margin:3px 15px 8px 3px !important;padding:24px 34px !important;
  border:5px solid #151515 !important;background-color:#fffdf5 !important;
  box-shadow:9px 9px 0 #151515;transform:rotate(.25deg);
}
.layout-chart-full .cf-chart-area::before{
  content:"FLOW!";position:absolute;right:22px;top:10px;z-index:3;
  padding:7px 12px;border:4px solid #151515;background:#ffd83d;color:#151515;
  font:900 .95rem/1 "Arial Black","Noto Sans SC",sans-serif;transform:rotate(4deg);
  box-shadow:4px 4px 0 #151515;
}
.layout-chart-full .cf-chart-area svg{width:78% !important;}

.layout-section .section-body{
  justify-content:flex-end !important;padding:58px 72px 72px !important;
  background:linear-gradient(154deg,transparent 0 37%,#ffd83d 37% 67%,#f0344b 67% 100%);
}
.layout-section .section-content{
  max-width:82% !important;background:#fffdf5;border:5px solid #151515;
  padding:20px 28px;box-shadow:10px 10px 0 #151515;transform:rotate(-1deg);
}
.layout-section .section-descriptor{
  max-width:70% !important;background:#fff;padding:9px 14px;border:3px solid #151515;margin-top:18px;
}
.layout-callout .ca-body{grid-template-columns:.9fr 1.1fr !important;}
.layout-callout .fm-callout-box{
  border:4px solid #151515 !important;border-radius:48% !important;
  box-shadow:7px 7px 0 #151515 !important;overflow:visible !important;transform:rotate(-1deg);
}
.layout-callout .fm-callout-box::after{
  content:"" !important;position:absolute;right:18%;bottom:-26px;width:42px;height:42px;
  background:inherit;border-right:4px solid #151515;border-bottom:4px solid #151515;
  transform:skew(25deg) rotate(32deg);
}
.layout-end .end-body{justify-content:center !important;padding:58px 70px !important;}
.layout-end .end-text{
  max-width:720px !important;background:#ffd83d;border:5px solid #151515;
  padding:34px 42px;box-shadow:12px 12px 0 #151515;transform:rotate(-1deg);
}
.layout-end .end-title{font-size:4.2rem !important;letter-spacing:-.05em !important;}
"""


NOTEBOOK_LAYOUT_CSS = r"""
/* Notebook: taped cover card, dated research log and asymmetric pinboard. */
.layout-cover .cover-body{
  align-items:flex-start !important;justify-content:flex-start !important;
  padding:72px 92px 52px !important;
}
.layout-cover .cover-frame{
  width:68% !important;max-width:none !important;margin:28px 0 0 !important;
  padding:38px 44px 42px !important;background:#fffef9 !important;
  border:1px solid #cfc4ab !important;box-shadow:6px 9px 22px rgba(67,54,33,.18) !important;
  transform:rotate(-1.1deg) !important;
}
.layout-cover .cover-frame::before{
  content:"";position:absolute;left:50%;top:-14px;width:112px;height:28px;
  transform:translateX(-50%) rotate(1.5deg);
  background:rgba(229,205,153,.78);border-left:1px dashed rgba(109,91,56,.25);
  border-right:1px dashed rgba(109,91,56,.25);
}
.layout-cover .cover-frame::after{
  content:"研究记录\A 07 / 24\A\A 关键词\A 文章 · 声音\A 画面 · 审阅";
  white-space:pre;position:absolute;left:calc(100% + 55px);top:18px;width:170px;
  padding:22px 20px 26px;background:#fff1a8;color:#40505e;
  font:500 .82rem/1.65 "KaiTi","Microsoft YaHei",sans-serif;
  box-shadow:4px 6px 12px rgba(67,54,33,.16);transform:rotate(2.4deg);
}
.layout-cover .cover-doc-number{
  color:#477eb4 !important;text-transform:none !important;font-size:.8rem !important;
}
.layout-cover .cover-title{
  font-family:"Noto Serif SC","Songti SC",serif !important;
  font-size:3.75rem !important;line-height:1.05 !important;letter-spacing:-.035em !important;
  text-align:left !important;margin-bottom:18px !important;
}
.layout-cover .cover-subtitle{
  max-width:610px !important;font-size:1.08rem !important;border-left:4px solid #d95b5b;
  padding-left:16px;
}
.layout-cover .cover-rule-top{height:2px !important;}
.layout-cover .cover-rule-mid{width:64% !important;height:2px !important;}

.layout-timeline .tl-track--horizontal{
  display:grid !important;grid-template-columns:1fr !important;
  gap:2px !important;padding:0 !important;margin:0 !important;overflow:visible !important;
}
.layout-timeline .tl-track--horizontal::before{display:none !important;}
.layout-timeline .tl-track--horizontal .tl-entry{
  display:block !important;min-width:0 !important;border-bottom:1px dashed rgba(71,126,180,.55);
}
.layout-timeline .tl-entry-marker{display:none !important;}
.layout-timeline .tl-entry-body{
  display:grid !important;grid-template-columns:85px 190px 1fr !important;
  align-items:center !important;text-align:left !important;padding:10px 10px 10px 18px !important;gap:12px;
}
.layout-timeline .tl-entry-date{
  color:#d95b5b !important;font-family:"KaiTi","Microsoft YaHei",sans-serif !important;
  transform:rotate(-2deg);
}
.layout-timeline .tl-entry-title{font-size:1.03rem !important;color:#405d4b !important;}
.layout-timeline .tl-entry-desc{font-size:.84rem !important;line-height:1.4 !important;}

.layout-chart-full .cf-chart-area{
  margin:10px 38px 9px 14px !important;padding:28px 34px !important;
  border:1px solid #b9aa8c !important;background-color:#fffef9 !important;
  box-shadow:5px 7px 16px rgba(67,54,33,.15);transform:rotate(-.45deg);
}
.layout-chart-full .cf-chart-area::before{
  content:"";position:absolute;left:50%;top:-12px;z-index:3;width:96px;height:24px;
  transform:translateX(-50%) rotate(1.5deg);background:rgba(229,205,153,.76);
}
.layout-chart-full .cf-chart-area::after{
  content:"从左到右\A看依赖";
  white-space:pre;position:absolute;right:12px;bottom:16px;
  color:#d95b5b;font:600 .75rem/1.45 "KaiTi","Microsoft YaHei",sans-serif;
  transform:rotate(7deg);
}
.layout-chart-full .cf-chart-area svg{width:80% !important;}

.layout-dashboard .db-grid--4{
  grid-template-columns:1.25fr 1fr 1fr !important;grid-template-rows:repeat(2,minmax(0,1fr)) !important;
  gap:13px !important;overflow:visible !important;padding:5px 4px 4px;
}
.layout-dashboard .db-panel:nth-child(1){grid-column:1;grid-row:1 / 3;transform:rotate(-.7deg);}
.layout-dashboard .db-panel:nth-child(2){grid-column:2 / 4;grid-row:1;transform:rotate(.45deg);}
.layout-dashboard .db-panel:nth-child(3){grid-column:2;grid-row:2;transform:rotate(-.35deg);}
.layout-dashboard .db-panel:nth-child(4){grid-column:3;grid-row:2;transform:rotate(.7deg);}
.layout-dashboard .db-panel-content .fm-stat{font-size:2.35rem !important;}
.layout-dashboard .db-panel:nth-child(1) .db-panel-content .fm-stat{font-size:2.8rem !important;}
.layout-comparison .cmp-col--left{transform:rotate(-.5deg);background:#fffef9;box-shadow:3px 5px 12px rgba(67,54,33,.12);}
.layout-comparison .cmp-col--right{transform:rotate(.65deg);background:#fffef9;box-shadow:3px 5px 12px rgba(67,54,33,.12);}
.layout-callout .fm-callout-box{
  background:#fff1a8 !important;border:0 !important;box-shadow:4px 6px 14px rgba(67,54,33,.16);
  transform:rotate(1deg);padding:8px !important;
}
.layout-end .end-body{justify-content:flex-end !important;align-items:flex-end !important;padding:62px 82px !important;}
.layout-end .end-text{
  max-width:650px !important;background:#fffef9;border:1px solid #cfc4ab;
  padding:34px 40px;box-shadow:5px 8px 20px rgba(67,54,33,.16);transform:rotate(-.7deg);
}
.layout-end .end-text::before{
  content:"";position:absolute;left:50%;top:-12px;width:92px;height:24px;
  transform:translateX(-50%) rotate(1deg);background:rgba(229,205,153,.74);
}
"""


_THEME_CSS = {
    "blueprint": BLUEPRINT_CSS,
    "crt": CRT_CSS,
    "comic": COMIC_CSS,
    "notebook": NOTEBOOK_CSS,
}

_THEME_LAYOUT_CSS = {
    "blueprint": BLUEPRINT_LAYOUT_CSS,
    "crt": CRT_LAYOUT_CSS,
    "comic": COMIC_LAYOUT_CSS,
    "notebook": NOTEBOOK_LAYOUT_CSS,
}


def normalize_themes(value: object) -> list[str]:
    """Return a stable, deduplicated list of supported visual theme ids."""
    raw: Iterable[object]
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, Iterable):
        raw = value
    else:
        raw = [value] if value else []

    result: list[str] = []
    for item in raw:
        name = str(item or "").strip().lower()
        if not name or name not in SUPPORTED_THEMES or name in result:
            continue
        result.append(name)
    return result or [DEFAULT_THEME]


def theme_css(theme: str) -> str:
    """Return the shared density contract plus one visual skin."""
    name = normalize_themes([theme])[0]
    return (
        COMMON_DENSITY_CSS
        + "\n"
        + _THEME_CSS[name].strip()
        + "\n"
        + V2_SHARED_LAYOUT_CSS.strip()
        + "\n"
        + _THEME_LAYOUT_CSS[name].strip()
        + "\n"
    )
