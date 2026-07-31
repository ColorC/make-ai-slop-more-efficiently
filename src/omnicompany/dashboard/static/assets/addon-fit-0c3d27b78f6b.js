/**
 * Copyright (c) 2014-2024 The xterm.js authors. All rights reserved.
 * @license MIT
 *
 * Copyright (c) 2012-2013, Christopher Jeffrey (MIT License)
 * @license MIT
 *
 * Originally forked from (with the author's permission):
 *   Fabrice Bellard's javascript vt100 for jslinux:
 *   http://bellard.org/jslinux/
 *   Copyright (c) 2011 Fabrice Bellard
 */
var e=class{activate(e){this._terminal=e}dispose(){}fit(){let e=this.proposeDimensions();if(!e||!this._terminal||isNaN(e.cols)||isNaN(e.rows))return;let t=this._terminal._core;(this._terminal.rows!==e.rows||this._terminal.cols!==e.cols)&&(t._renderService.clear(),this._terminal.resize(e.cols,e.rows))}proposeDimensions(){var e;if(!this._terminal||!this._terminal.element||!this._terminal.element.parentElement)return;let t=this._terminal._core._renderService.dimensions;if(0===t.css.cell.width||0===t.css.cell.height)return;let r=0===this._terminal.options.scrollback?0:(null==(e=this._terminal.options.overviewRuler)?void 0:e.width)||14,i=window.getComputedStyle(this._terminal.element.parentElement),s=parseInt(i.getPropertyValue("height")),l=Math.max(0,parseInt(i.getPropertyValue("width"))),a=window.getComputedStyle(this._terminal.element),n=s-(parseInt(a.getPropertyValue("padding-top"))+parseInt(a.getPropertyValue("padding-bottom"))),o=l-(parseInt(a.getPropertyValue("padding-right"))+parseInt(a.getPropertyValue("padding-left")))-r;return{cols:Math.max(2,Math.floor(o/t.css.cell.width)),rows:Math.max(1,Math.floor(n/t.css.cell.height))}}};export{e as FitAddon};
