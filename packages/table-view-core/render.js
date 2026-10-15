/**
 * Dependency-free TableDoc/TableDiff renderer.
 *
 * The browser runtime is deliberately a self-contained function: renderTableHtml
 * serializes that exact function into the standalone document, while widget.js
 * calls it directly. This keeps the two surfaces on one DOM implementation.
 */

export const TABLE_VIEW_UI = Object.freeze({
  tables: "表格",
  diffOnly: "仅差异",
  allRows: "全量",
  noTables: "没有可显示的表格",
  noChanges: "当前表没有变更行。",
  noRows: "当前表没有数据",
  empty: "空",
  formulaBadge: "fx",
  formula: "公式",
  cachedValue: "缓存值",
  projectedValue: "投影值",
  sourceValue: "源侧值",
  before: "变更前",
  after: "变更后",
  changeKind: "变更类型",
  created: "新增行",
  deleted: "删除行",
  capacityTitle: "表格超过安全渲染容量",
  capacityDetail: "为避免浏览器失去响应，此表未生成单元格 DOM。",
  rows: "行数",
  cells: "单元格数",
  rowLimit: "行数上限",
  cellLimit: "单元格上限",
  tabList: "表格页签",
  formulaCell: "公式单元格",
  tabMenu: "表格链接",
  copied: "已复制",
  copyFailed: "复制失败",
});

export const TABLE_VIEW_CSS = String.raw`
:root {
  color-scheme: light;
  --tvc-page: #f3eee7;
  --tvc-paper: #fffdf8;
  --tvc-soft: #f8f3eb;
  --tvc-ink: #332c26;
  --tvc-muted: #766b61;
  --tvc-line: #ded5c8;
  --tvc-line-strong: #c8baa8;
  --tvc-accent: #9a512d;
  --tvc-accent-soft: #f7e9dd;
  --tvc-created: #e7eff7;
  --tvc-deleted: #ffe1dc;
  --tvc-kind-value: #fff0bd;
  --tvc-kind-formula: #e8ddff;
  --tvc-kind-formula-result: #dcecff;
  --tvc-kind-source: #ffe3c6;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--tvc-page); }
.tvc { color: var(--tvc-ink); background: var(--tvc-page); font: 13px/1.45 "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif; }
.tvc button, .tvc input { font: inherit; }
.tvc-shell { width: min(1680px, calc(100% - 28px)); margin: 14px auto 36px; }
.tvc-tab:hover { background: #f7f1e8; }
.tvc-workbook { background: var(--tvc-paper); border: 1px solid var(--tvc-line-strong); }
.tvc-workbook-bar { display: flex; min-height: 34px; background: #eadfd1; border-bottom: 1px solid var(--tvc-line-strong); }
.tvc-tabs { display: flex; flex: 1 1 auto; min-width: 0; min-height: 34px; overflow-x: auto; scrollbar-width: none; }
.tvc-tabs::-webkit-scrollbar { display: none; }
.tvc-tab { position: relative; flex: 0 0 auto; min-width: 128px; min-height: 34px; padding: 4px 10px 3px; color: #66594e; background: #f2eadf; border: 0; border-right: 1px solid var(--tvc-line-strong); cursor: pointer; text-align: left; }
.tvc-tab[aria-selected="true"] { color: var(--tvc-ink); background: var(--tvc-paper); font-weight: 700; }
.tvc-tab[aria-selected="true"]::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 3px; content: ""; background: var(--tvc-accent); }
.tvc-tab-name { display: block; }
.tvc-tab-source { display: block; margin-top: 1px; color: var(--tvc-muted); font-size: 10px; font-weight: 400; max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tvc-tab[data-has-links="true"] { cursor: context-menu; }
.tvc-context-menu { position: fixed; z-index: 10001; min-width: 220px; max-width: min(560px, calc(100vw - 20px)); margin: 0; padding: 4px; list-style: none; color: var(--tvc-ink); background: var(--tvc-paper); border: 1px solid var(--tvc-line-strong); border-radius: 6px; box-shadow: 0 10px 26px rgba(0,0,0,.22); }
.tvc-context-menu-title { padding: 5px 10px 3px; color: var(--tvc-muted); font-size: 10px; letter-spacing: .02em; }
.tvc-context-menu-item { display: block; width: 100%; padding: 6px 10px; color: inherit; background: transparent; border: 0; border-radius: 4px; text-align: left; cursor: pointer; }
.tvc-context-menu-item:hover, .tvc-context-menu-item:focus-visible { background: var(--tvc-accent-soft); outline: 0; }
.tvc-context-menu-item small { display: block; margin-top: 1px; color: var(--tvc-muted); font: 10px/1.4 Consolas, "SFMono-Regular", monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tvc-context-menu-item[data-state="copied"] { color: #1f5a2c; background: #e6f4e8; }
.tvc-context-menu-item[data-state="failed"] { color: #7c2923; background: #fff1ef; }
.tvc-mode { display: flex; flex: 0 0 auto; align-items: center; gap: 2px; padding: 3px; border-left: 1px solid var(--tvc-line-strong); }
.tvc-mode-button { min-width: 58px; height: 27px; padding: 0 9px; color: #66594e; background: transparent; border: 0; cursor: pointer; }
.tvc-mode-button[aria-pressed="true"] { color: #fff; background: var(--tvc-accent); font-weight: 700; }
.tvc-sheet { overflow-x: auto; background: var(--tvc-paper); }
.tvc-table { width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; }
.tvc-table th, .tvc-table td { min-width: 0; padding: 8px 10px; vertical-align: top; background: var(--tvc-paper); border-right: 1px solid var(--tvc-line); border-bottom: 1px solid var(--tvc-line); text-align: left; overflow-wrap: anywhere; }
.tvc-table th { position: sticky; top: var(--tvc-header-top, 0); z-index: 3; height: 34px; padding-top: 6px; padding-bottom: 6px; color: #4d4035; background: #f2eadf; font-weight: 750; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tvc-table th:last-child, .tvc-table td:last-child { border-right: 0; }
.tvc-header-role-type { color: var(--tvc-accent) !important; font-family: Consolas, "SFMono-Regular", monospace; }
.tvc-header-role-name { color: #3f352d !important; font-family: Consolas, "SFMono-Regular", monospace; }
.tvc-key { position: sticky; left: var(--tvc-key-left, 0); z-index: 2; background: #f8f3eb !important; font-family: Consolas, "SFMono-Regular", monospace; }
.tvc-table th.tvc-key { z-index: 5; background: #eadfd1 !important; }
.tvc-last-key { box-shadow: 5px 0 7px -6px rgba(0,0,0,.75); }
.tvc-value { display: block; font: 12px Consolas, "SFMono-Regular", monospace; white-space: pre-wrap; }
.tvc-empty { color: #858a85; font-style: italic; }
.tvc-cell-detail { position: relative; cursor: help; outline: none; }
.tvc-cell-detail:focus-visible { box-shadow: inset 0 0 0 2px var(--tvc-accent); }
.tvc-before { color: #6a5a41; text-decoration: line-through; text-decoration-thickness: 1px; }
.tvc-after { margin-top: 3px; padding-top: 3px; border-top: 1px solid rgba(70,70,50,.2); }
.tvc-fx-badge { position: absolute; top: 3px; right: 4px; padding: 0 4px; color: #fff; background: #5d4a91; border-radius: 3px; font: 700 10px/16px Consolas, monospace; }
.tvc-kind-value { background: var(--tvc-kind-value) !important; }
.tvc-kind-formula { background: var(--tvc-kind-formula) !important; }
.tvc-kind-formula-result { background: var(--tvc-kind-formula-result) !important; }
.tvc-kind-source { background: var(--tvc-kind-source) !important; }
.tvc-row-created > td { background: var(--tvc-created) !important; }
.tvc-row-deleted > td { color: #73251f; background: var(--tvc-deleted) !important; }
.tvc-row-deleted .tvc-value { text-decoration: line-through; }
.tvc-empty-state, .tvc-error { padding: 44px 20px; color: var(--tvc-muted); text-align: center; }
.tvc-error { margin: 20px; color: #7c2923; background: #fff1ef; border: 1px solid #e6b4ae; }
.tvc-error-title { margin: 0 0 8px; font-size: 17px; }
.tvc-error-detail { margin: 0; }
.tvc-error-stats { margin: 10px 0 0; font: 12px Consolas, monospace; }
.tvc-tooltip { position: fixed; z-index: 10000; display: none; width: min(420px, calc(100vw - 20px)); max-height: min(70vh, 520px); overflow: auto; padding: 10px 12px; color: #fffaf4; background: #302821; border: 1px solid #1f1914; box-shadow: 0 8px 22px rgba(0,0,0,.22); pointer-events: none; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.55 Consolas, "SFMono-Regular", monospace; }
.tvc-tooltip.tvc-visible { display: block; }
.tvc-wiki-field { color: inherit; text-decoration: none; border-bottom: 1px dashed #b07a5b; }
.tvc-wiki-field:hover, .tvc-wiki-field:focus-visible { color: var(--tvc-accent); background: var(--tvc-accent-soft); outline: 0; }
@media (max-width: 720px) {
  .tvc-shell { width: 100%; margin: 0 0 20px; }
  .tvc-workbook { border-right: 0; border-left: 0; }
  .tvc-tab { min-width: 132px; }
}
@media print {
  @page { size: A4 landscape; margin: 10mm; }
  body, .tvc { background: #fff; }
  .tvc-shell { width: 100%; margin: 0; }
  .tvc-workbook-bar { display: none; }
  .tvc-workbook { border: 0; }
  .tvc-sheet { overflow: visible; }
  .tvc-table th, .tvc-table td { position: static; }
}
`;

const MAX_ROWS = 10_000;
const MAX_CELLS = 250_000;

/**
 * Creates the shared browser-side DOM renderer. Keep every runtime dependency
 * inside this function so its source can be embedded verbatim in standalone HTML.
 */
export function createTableViewRuntime(host, ui) {
  const document = host.document;
  const ROW_LIMIT = 10000;
  const CELL_LIMIT = 250000;
  const DEFAULT_ROLES = ["label", "type", "name"];

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object || {}, key);
  }

  function stableJson(value) {
    if (value === undefined) return "null";
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return "[" + value.map(stableJson).join(",") + "]";
    return "{" + Object.keys(value).sort().map(function (key) {
      return JSON.stringify(key) + ":" + stableJson(value[key]);
    }).join(",") + "}";
  }

  function sameValue(left, right) {
    return stableJson(left) === stableJson(right);
  }

  function cellParts(cell) {
    if (cell === null || cell === undefined) return { v: null };
    if (typeof cell !== "object" || Array.isArray(cell)) return { v: cell };
    if (own(cell, "v") || own(cell, "sv") || own(cell, "f") || own(cell, "fv")) return cell;
    return { v: cell };
  }

  function valueText(value) {
    if (value === null || value === undefined || value === "") return ui.empty;
    if (typeof value === "string") return value;
    const serialized = stableJson(value);
    return serialized === undefined ? String(value) : serialized;
  }

  function cellTooltip(cell, prefix) {
    const parts = cellParts(cell);
    const lines = [];
    const label = prefix ? prefix + " · " : "";
    if (parts.f !== null && parts.f !== undefined) {
      lines.push(label + ui.formula + ": " + valueText(parts.f));
      lines.push(label + ui.cachedValue + ": " + valueText(parts.fv));
      lines.push(label + ui.projectedValue + ": " + valueText(parts.v));
    }
    if (own(parts, "sv") && !sameValue(parts.sv, parts.v)) {
      lines.push(label + ui.sourceValue + ": " + valueText(parts.sv));
      if (parts.f === null || parts.f === undefined) lines.push(label + ui.projectedValue + ": " + valueText(parts.v));
    }
    return lines;
  }

  function appendValue(cell, parts, className) {
    const empty = parts.v === null || parts.v === undefined || parts.v === "";
    cell.appendChild(make("span", "tvc-value " + className + (empty ? " tvc-empty" : ""), valueText(parts.v)));
  }

  function decorateDetails(cell, tooltipLines, parts) {
    if (parts && parts.f !== null && parts.f !== undefined) {
      cell.appendChild(make("span", "tvc-fx-badge", ui.formulaBadge));
    }
    if (!tooltipLines.length) return;
    cell.classList.add("tvc-cell-detail");
    cell.tabIndex = 0;
    cell.dataset.tooltip = tooltipLines.join("\n");
    cell.setAttribute("aria-label", tooltipLines.join("；"));
  }

  function keyIdentity(keyFields, keyObject) {
    return stableJson(keyFields.map(function (field) { return [field, keyObject ? keyObject[field] : null]; }));
  }

  function rowKey(row, keyFields) {
    const key = {};
    keyFields.forEach(function (field) { key[field] = cellParts(row && row.cells ? row.cells[field] : null).v; });
    return key;
  }

  /**
   * 「带 diff 壳但一条变更标记都没有」的表：diff 对象在，created/changed/deleted 三个清单全空。
   * 2026-09-16 用户裁定「没有变更行不是坏了，但这不合理」—— 这种表若停在「仅差异」会被过滤成空，
   * 界面手里有行却显示"当前表没有变更行"。判出来后初始就落到「全量」。
   * 注意: 必须定义在本运行时闭包内 —— renderTableHtml 用 createTableViewRuntime.toString()
   * 把运行时序列化进独立 HTML, 引用闭包外的函数在那份 HTML 里会 ReferenceError。
   */
  function isEmptyDiffShell(item) {
    if (!item || typeof item !== "object" || !item.diff) return false;
    const diff = item.diff;
    let marks = 0;
    ["created", "changed", "deleted"].forEach(function (key) {
      if (Array.isArray(diff[key])) marks += diff[key].length;
    });
    if (marks > 0) return false;
    const table = item.doc && item.doc.table;
    const rows = table && Array.isArray(table.rows) ? table.rows : [];
    return rows.length > 0;
  }

  /** 初始处于「全量」模式的表下标：显式 default_mode=all，或空 diff 壳。 */
  function initialShowAllIndices(docs, defaultMode) {
    const list = Array.isArray(docs) ? docs : [];
    return list
      .map(function (_item, index) { return index; })
      .filter(function (index) { return defaultMode === "all" || isEmptyDiffShell(list[index]); });
  }

  function buildModel(item) {
    const table = item.doc.table;
    const columns = (Array.isArray(table.columns) ? table.columns : []).filter(function (column) {
      return column && column.name !== "__p4_key";
    });
    const keyFields = Array.isArray(table.key_fields) ? table.key_fields : [];
    const keySet = new Set(keyFields);
    columns.forEach(function (column) { if (column.key) keySet.add(column.name); });
    const rows = [];
    const rowMap = new Map();

    (table.rows || []).forEach(function (sourceRow) {
      const key = rowKey(sourceRow, keyFields);
      const modelRow = { key: key, cells: sourceRow.cells || {}, changes: {}, created: false, deleted: false, changed: false };
      rows.push(modelRow);
      rowMap.set(keyIdentity(keyFields, key), modelRow);
    });

    const diff = item.diff || {};
    (diff.created || []).forEach(function (entry) {
      const identity = keyIdentity(keyFields, entry.key || {});
      let modelRow = rowMap.get(identity);
      if (!modelRow) {
        modelRow = { key: entry.key || {}, cells: entry.after || {}, changes: {}, created: true, deleted: false, changed: true };
        rows.push(modelRow);
        rowMap.set(identity, modelRow);
      }
      modelRow.cells = Object.assign({}, modelRow.cells, entry.after || {});
      modelRow.created = true;
      modelRow.changed = true;
    });
    (diff.updated || []).forEach(function (entry) {
      const identity = keyIdentity(keyFields, entry.key || {});
      let modelRow = rowMap.get(identity);
      if (!modelRow) {
        modelRow = { key: entry.key || {}, cells: {}, changes: {}, created: false, deleted: false, changed: true };
        rows.push(modelRow);
        rowMap.set(identity, modelRow);
      }
      modelRow.changes = Object.assign({}, modelRow.changes, entry.changes || {});
      modelRow.changed = true;
    });
    (diff.deleted || []).forEach(function (entry) {
      const identity = keyIdentity(keyFields, entry.key || {});
      let modelRow = rowMap.get(identity);
      if (!modelRow) {
        modelRow = { key: entry.key || {}, cells: entry.before || {}, changes: {}, created: false, deleted: true, changed: true };
        rows.push(modelRow);
        rowMap.set(identity, modelRow);
      }
      modelRow.cells = Object.assign({}, modelRow.cells, entry.before || {});
      modelRow.deleted = true;
      modelRow.changed = true;
    });

    return { table: table, columns: columns, keyFields: keyFields, keySet: keySet, rows: rows, hasDiff: Boolean(item.diff) };
  }

  function visualUnits(value) {
    return Array.from(valueText(value)).reduce(function (total, character) {
      return total + (character.codePointAt(0) > 255 ? 1 : 0.62);
    }, 0);
  }

  function fittedWidths(model) {
    return model.columns.map(function (column) {
      let longest = Math.max(visualUnits(column.label || ""), visualUnits(column.type || ""), visualUnits(column.name || ""));
      model.rows.slice(0, 250).forEach(function (row) {
        const change = row.changes[column.name];
        if (change) {
          longest = Math.max(longest, visualUnits(cellParts(change.before).v), visualUnits(cellParts(change.after).v));
        } else {
          longest = Math.max(longest, visualUnits(cellParts(row.cells[column.name]).v));
        }
      });
      const isKey = model.keySet.has(column.name);
      const minimum = isKey ? 104 : 126;
      const maximum = isKey ? 224 : 360;
      return Math.round(Math.max(minimum, Math.min(maximum, 30 + longest * 12.5)));
    });
  }

  function headerRoles(table, columns) {
    const layout = table.header_layout || {};
    let roles = Array.isArray(layout.roles) && layout.roles.length ? layout.roles.slice() : DEFAULT_ROLES.slice();
    if (Number.isInteger(layout.rows) && layout.rows >= 0) {
      if (roles.length > layout.rows) roles = roles.slice(0, layout.rows);
      while (roles.length < layout.rows) roles.push("name");
    }
    const signatures = new Set();
    return roles.filter(function (role) {
      const signature = (columns || []).map(function (column) { return roleText(column, role); }).join("\u001f");
      if (!signature.replace(/\u001f/g, "").trim() || signatures.has(signature)) return false;
      signatures.add(signature);
      return true;
    });
  }

  function roleText(column, role) {
    if (role === "description") return column.label || "";
    if (role === "label" || role === "type" || role === "name") return column[role] || "";
    return own(column, role) ? valueText(column[role]) : "";
  }

  function safeClass(value) {
    return String(value).toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  function makeCapacityError(table, capacity) {
    const rowCount = capacity ? capacity.rows : (table.rows || []).length;
    const cellCount = capacity ? capacity.cells : rowCount * (table.columns || []).length;
    const card = make("section", "tvc-error");
    card.append(
      make("h2", "tvc-error-title", ui.capacityTitle),
      make("p", "tvc-error-detail", ui.capacityDetail),
      make("p", "tvc-error-stats", ui.rows + ": " + rowCount + " / " + ui.rowLimit + ": " + ROW_LIMIT + " · " + ui.cells + ": " + cellCount + " / " + ui.cellLimit + ": " + CELL_LIMIT)
    );
    return card;
  }

  function makeCell(model, row, column) {
    const change = row.changes[column.name];
    const raw = row.cells[column.name];
    const cell = make("td", "");
    cell.dataset.field = column.name;
    const tooltip = [];
    let details;
    if (change) {
      cell.dataset.changeKind = change.kind || "value";
      const before = cellParts(change.before);
      const after = cellParts(change.after);
      cell.classList.add("tvc-kind-" + safeClass(change.kind || "value"));
      appendValue(cell, before, "tvc-before");
      appendValue(cell, after, "tvc-after");
      tooltip.push(ui.changeKind + ": " + (change.kind || "value"));
      tooltip.push.apply(tooltip, cellTooltip(before, ui.before));
      tooltip.push.apply(tooltip, cellTooltip(after, ui.after));
      if (!cellTooltip(before, ui.before).length) tooltip.push(ui.before + " · " + ui.projectedValue + ": " + valueText(before.v));
      if (!cellTooltip(after, ui.after).length) tooltip.push(ui.after + " · " + ui.projectedValue + ": " + valueText(after.v));
      details = after;
    } else {
      details = cellParts(raw);
      appendValue(cell, details, "");
      tooltip.push.apply(tooltip, cellTooltip(details, ""));
    }

    if (model.keySet.has(column.name)) {
      cell.classList.add("tvc-key");
    }
    decorateDetails(cell, tooltip, details);
    return cell;
  }

  function renderTable(item, showAll) {
    const tableData = item && item.doc && item.doc.table;
    if (!tableData) return make("div", "tvc-error", ui.noTables);
    const rowCount = (tableData.rows || []).length;
    const cellCount = rowCount * (tableData.columns || []).length;
    if (item.capacity_error || rowCount > ROW_LIMIT || cellCount > CELL_LIMIT) {
      return makeCapacityError(tableData, item.capacity_error);
    }

    const model = buildModel(item);
    const renderedCellCount = model.rows.length * model.columns.length;
    if (model.rows.length > ROW_LIMIT || renderedCellCount > CELL_LIMIT) {
      return makeCapacityError(tableData, { rows: model.rows.length, cells: renderedCellCount });
    }
    const diffRows = model.hasDiff ? model.rows.filter(function (row) { return row.changed; }) : model.rows;
    // 2026-09-16 用户裁定「没有变更行不是坏了，但这不合理」：报表带了 diff 壳、行却一条
    // 变更标记都没有时，原来会显示"当前表没有变更行"并把已有的行全部藏起来 —— 界面手里
    // 有数据却说没有。这种表按全量显示（mount 处同时把该表的模式初始为全量，按钮状态一致）。
    const rows = showAll || !model.hasDiff || diffRows.length === 0 ? model.rows : diffRows;
    if (!rows.length) return make("div", "tvc-empty-state", ui.noRows);
    const widths = fittedWidths(model);
    const table = make("table", "tvc-table");
    const colgroup = document.createElement("colgroup");
    widths.forEach(function (width) {
      const col = document.createElement("col");
      col.style.width = width + "px";
      colgroup.appendChild(col);
    });
    table.style.minWidth = widths.reduce(function (sum, width) { return sum + width; }, 0) + "px";
    table.appendChild(colgroup);

    const thead = document.createElement("thead");
    // 字段身份不能被展示标签覆盖：中文 label 进入语义提示，表头始终显示真实字段名。
    const roles = headerRoles({ header_layout: { roles: ["name", "type"], rows: 2 } }, model.columns);
    const keyOffsets = new Map();
    let keyLeft = 0;
    model.columns.forEach(function (column, index) {
      if (!model.keySet.has(column.name)) return;
      keyOffsets.set(column.name, keyLeft);
      keyLeft += widths[index];
    });
    const keyNames = model.columns.filter(function (column) { return model.keySet.has(column.name); }).map(function (column) { return column.name; });
    roles.forEach(function (role, roleIndex) {
      const tr = document.createElement("tr");
      model.columns.forEach(function (column) {
        const label = roleText(column, role);
        const th = make("th", "tvc-header-role-" + safeClass(role), label);
        const reference = item.references && item.references.fields ? item.references.fields[column.name] : null;
        if (reference && reference.href) {
          const link = make("a", "tvc-wiki-field", label);
          link.href = reference.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.dataset.materialRef = reference.material_ref || "";
          th.replaceChildren(link);
          if (reference.description) {
            th.dataset.tooltip = reference.description;
            th.setAttribute("aria-label", label + "；" + reference.description);
          }
        }
        const isKey = model.keySet.has(column.name);
        th.style.setProperty("--tvc-header-top", roleIndex * 34 + "px");
        if (isKey) {
          th.classList.add("tvc-key");
          th.style.setProperty("--tvc-key-left", keyOffsets.get(column.name) + "px");
          if (column.name === keyNames[keyNames.length - 1]) th.classList.add("tvc-last-key");
        }
        tr.appendChild(th);
      });
      thead.appendChild(tr);
    });
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      const tr = document.createElement("tr");
      tr.dataset.key = JSON.stringify(row.key);
      if (row.created) {
        tr.classList.add("tvc-row-created");
        tr.dataset.rowKind = "created";
        tr.setAttribute("aria-label", ui.created);
      }
      if (row.deleted) {
        tr.classList.add("tvc-row-deleted");
        tr.dataset.rowKind = "deleted";
        tr.setAttribute("aria-label", ui.deleted);
      }
      model.columns.forEach(function (column) {
        const td = makeCell(model, row, column);
        if (model.keySet.has(column.name)) {
          td.style.setProperty("--tvc-key-left", keyOffsets.get(column.name) + "px");
          if (column.name === keyNames[keyNames.length - 1]) td.classList.add("tvc-last-key");
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function mount(element, report) {
    if (!element || element.nodeType !== 1) throw new TypeError("mountTableView requires a DOM element");
    const data = report && typeof report === "object" ? report : {};
    const docs = Array.isArray(data.docs) ? data.docs : [];
    const hasDiff = docs.some(function (item) { return Boolean(item && item.diff); });
    const state = {
      active: 0,
      showAll: new Set(initialShowAllIndices(docs, data.default_mode)),
    };
    element.replaceChildren();
    element.classList.add("tvc");

    const shell = make("main", "tvc-shell");
    const workbook = make("section", "tvc-workbook");
    const workbookBar = make("div", "tvc-workbook-bar");
    const tabs = make("nav", "tvc-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", ui.tabList);
    const mode = make("div", "tvc-mode");
    const diffOnlyButton = make("button", "tvc-mode-button", ui.diffOnly);
    const allRowsButton = make("button", "tvc-mode-button", ui.allRows);
    diffOnlyButton.type = "button";
    allRowsButton.type = "button";
    mode.append(diffOnlyButton, allRowsButton);
    const sheet = make("div", "tvc-sheet");
    const tooltip = make("div", "tvc-tooltip");
    tooltip.setAttribute("role", "tooltip");
    tooltip.setAttribute("aria-hidden", "true");
    // 表格标题（页签）在只有一张表时也要显示：标题第二行写「基于 …」（P4 版本 / changelist /
    // 本地文件），右键能复制本机路径与 P4 链接。没有这些信息的单表报表维持原样不占一行。
    const sourceLinks = function (item) {
      const source = item && item.doc && item.doc.source ? item.doc.source : {};
      return Array.isArray(source.links) ? source.links.filter(function (link) {
        return link && typeof link.value === "string" && link.value && typeof link.label === "string";
      }) : [];
    };
    const hasTitleBar = docs.some(function (item) {
      const source = item && item.doc && item.doc.source ? item.doc.source : {};
      return Boolean(source.basis) || sourceLinks(item).length > 0;
    });
    if (docs.length > 1 || hasTitleBar) workbookBar.appendChild(tabs);
    if (hasDiff) workbookBar.appendChild(mode);
    if (docs.length > 1 || hasDiff || hasTitleBar) workbook.appendChild(workbookBar);
    workbook.appendChild(sheet);
    shell.appendChild(workbook);
    element.append(shell, tooltip);

    let contextMenu = null;
    function closeContextMenu() {
      if (!contextMenu) return;
      contextMenu.remove();
      contextMenu = null;
      document.removeEventListener("pointerdown", onDocumentPointerDown, true);
      document.removeEventListener("keydown", onDocumentKeyDown, true);
      host.removeEventListener("scroll", closeContextMenu, true);
      host.removeEventListener("blur", closeContextMenu);
    }
    function onDocumentPointerDown(event) {
      if (contextMenu && !contextMenu.contains(event.target)) closeContextMenu();
    }
    function onDocumentKeyDown(event) {
      if (event.key === "Escape") closeContextMenu();
    }
    function copyText(text) {
      if (host.navigator && host.navigator.clipboard && typeof host.navigator.clipboard.writeText === "function") {
        return host.navigator.clipboard.writeText(text).catch(function () { return copyTextFallback(text); });
      }
      return copyTextFallback(text);
    }
    function copyTextFallback(text) {
      return new Promise(function (resolve, reject) {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch (error) { void error; }
        area.remove();
        if (ok) resolve(); else reject(new Error("copy failed"));
      });
    }
    function openContextMenu(item, event) {
      const links = sourceLinks(item);
      if (!links.length) return;
      event.preventDefault();
      closeContextMenu();
      const table = item && item.doc && item.doc.table ? item.doc.table : {};
      const menu = make("ul", "tvc-context-menu");
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", ui.tabMenu + (table.name ? "：" + table.name : ""));
      menu.appendChild(make("li", "tvc-context-menu-title", table.name || ui.tabMenu));
      links.forEach(function (link) {
        const li = document.createElement("li");
        li.setAttribute("role", "none");
        const button = make("button", "tvc-context-menu-item");
        button.type = "button";
        button.setAttribute("role", "menuitem");
        button.dataset.linkKind = link.kind || "";
        button.append(make("span", "tvc-context-menu-label", link.label), make("small", null, link.value));
        button.title = link.value;
        button.addEventListener("click", function () {
          copyText(link.value).then(function () {
            button.dataset.state = "copied";
            button.querySelector(".tvc-context-menu-label").textContent = ui.copied + " · " + link.label;
            host.setTimeout(closeContextMenu, 700);
          }, function () {
            button.dataset.state = "failed";
            button.querySelector(".tvc-context-menu-label").textContent = ui.copyFailed + " · " + link.label;
          });
        });
        li.appendChild(button);
        menu.appendChild(li);
      });
      document.body.appendChild(menu);
      const width = menu.offsetWidth;
      const height = menu.offsetHeight;
      let left = event.clientX;
      let top = event.clientY;
      if (left + width > host.innerWidth - 8) left = Math.max(8, host.innerWidth - width - 8);
      if (top + height > host.innerHeight - 8) top = Math.max(8, event.clientY - height);
      menu.style.left = left + "px";
      menu.style.top = top + "px";
      contextMenu = menu;
      document.addEventListener("pointerdown", onDocumentPointerDown, true);
      document.addEventListener("keydown", onDocumentKeyDown, true);
      host.addEventListener("scroll", closeContextMenu, true);
      host.addEventListener("blur", closeContextMenu);
      const first = menu.querySelector(".tvc-context-menu-item");
      if (first) first.focus();
    }

    function renderTabs() {
      tabs.replaceChildren();
      docs.forEach(function (item, index) {
        const table = item && item.doc && item.doc.table ? item.doc.table : {};
        const source = item && item.doc && item.doc.source ? item.doc.source : {};
        const links = sourceLinks(item);
        const button = make("button", "tvc-tab");
        button.type = "button";
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", String(index === state.active));
        button.tabIndex = index === state.active ? 0 : -1;
        const basis = source.basis || source.locator || source.adapter || "";
        button.append(make("span", "tvc-tab-name", table.name || (ui.tables + " " + (index + 1))), make("span", "tvc-tab-source", basis));
        if (basis) button.title = basis + (links.length ? "\n" + ui.tabMenu + "：右键" : "");
        if (links.length) {
          button.dataset.hasLinks = "true";
          button.addEventListener("contextmenu", function (event) { openContextMenu(item, event); });
        }
        button.addEventListener("click", function () { state.active = index; renderTabs(); renderSheet(); });
        button.addEventListener("keydown", function (event) {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          const delta = event.key === "ArrowRight" ? 1 : -1;
          state.active = (state.active + delta + docs.length) % docs.length;
          renderTabs();
          renderSheet();
          const activeButton = tabs.querySelectorAll(".tvc-tab")[state.active];
          if (activeButton) activeButton.focus();
        });
        tabs.appendChild(button);
      });
    }

    function renderSheet() {
      const showAll = state.showAll.has(state.active);
      diffOnlyButton.setAttribute("aria-pressed", String(!showAll));
      allRowsButton.setAttribute("aria-pressed", String(showAll));
      sheet.replaceChildren();
      if (!docs.length) {
        sheet.appendChild(make("div", "tvc-empty-state", ui.noTables));
        return;
      }
      sheet.appendChild(renderTable(docs[state.active], showAll));
    }

    diffOnlyButton.addEventListener("click", function () {
      state.showAll.delete(state.active);
      renderSheet();
    });
    allRowsButton.addEventListener("click", function () {
      state.showAll.add(state.active);
      renderSheet();
    });

    function positionTooltip(event) {
      const gap = 14;
      let left = event.clientX + gap;
      let top = event.clientY + gap;
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      if (left + width > host.innerWidth - 8) left = event.clientX - width - gap;
      if (top + height > host.innerHeight - 8) top = event.clientY - height - gap;
      tooltip.style.left = Math.max(8, left) + "px";
      tooltip.style.top = Math.max(8, top) + "px";
    }

    function showTooltip(target, event) {
      if (!target || !target.dataset.tooltip) return;
      tooltip.textContent = target.dataset.tooltip;
      tooltip.classList.add("tvc-visible");
      tooltip.setAttribute("aria-hidden", "false");
      positionTooltip(event);
    }

    function hideTooltip() {
      tooltip.classList.remove("tvc-visible");
      tooltip.setAttribute("aria-hidden", "true");
    }

    function detailTarget(target) {
      const found = target && target.closest ? target.closest("[data-tooltip]") : null;
      return found && element.contains(found) ? found : null;
    }

    element.addEventListener("pointerover", function (event) {
      const target = detailTarget(event.target);
      if (target) showTooltip(target, event);
    });
    element.addEventListener("pointermove", function (event) {
      if (tooltip.classList.contains("tvc-visible")) positionTooltip(event);
    });
    element.addEventListener("pointerout", function (event) {
      const from = detailTarget(event.target);
      const to = detailTarget(event.relatedTarget);
      if (from && from !== to) hideTooltip();
    });
    element.addEventListener("focusin", function (event) {
      const target = detailTarget(event.target);
      if (!target) return;
      const rect = target.getBoundingClientRect();
      showTooltip(target, { clientX: rect.left + Math.min(rect.width, 80), clientY: rect.bottom });
    });
    element.addEventListener("focusout", hideTooltip);

    // 结构化单元格引用回调（仅 widget 模式可用：renderTableHtml 的 JSON 序列化
    // 会自然丢掉函数成员，静态页不受影响）。消费方用它做"复制引用"一类交互。
    if (typeof data.onCellSelect === "function") {
      element.addEventListener("click", function (event) {
        const cell = event.target && event.target.closest ? event.target.closest("td[data-field]") : null;
        if (!cell || !element.contains(cell)) return;
        const row = cell.closest("tr[data-key]");
        const item = docs[state.active];
        let key = null;
        try { key = row ? JSON.parse(row.dataset.key) : null; } catch (error) { void error; }
        data.onCellSelect({
          table: item && item.doc && item.doc.table ? item.doc.table.name : "",
          source: item && item.doc ? item.doc.source : null,
          key: key,
          field: cell.dataset.field,
          kind: cell.dataset.changeKind || null,
          rowKind: row ? row.dataset.rowKind || null : null,
          text: cell.textContent,
          tooltip: cell.dataset.tooltip || "",
        });
      });
    }

    renderTabs();
    renderSheet();
    const placeholder = ["__", "PLACEHOLDER", "__"].join("");
    if (element.innerHTML.includes(placeholder)) throw new Error("Unresolved template placeholder");
    return element;
  }

  return { mount: mount, isEmptyDiffShell: isEmptyDiffShell, initialShowAllIndices: initialShowAllIndices };
}

function safeScriptJson(value) {
  const json = JSON.stringify(value);
  if (json === undefined) throw new TypeError("report must be JSON-serializable");
  return json
    .replace(/&/g, "\\u0026")
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function prepareStandaloneReport(report) {
  const prepared = Object.assign({}, report);
  if (!Array.isArray(report.docs)) return prepared;
  prepared.docs = report.docs.map(function (item) {
    const table = item && item.doc && item.doc.table;
    if (!table) return item;
    const rows = Array.isArray(table.rows) ? table.rows : [];
    const columns = Array.isArray(table.columns) ? table.columns : [];
    const cells = rows.length * columns.length;
    if (rows.length <= MAX_ROWS && cells <= MAX_CELLS) return item;
    return Object.assign({}, item, {
      capacity_error: { rows: rows.length, cells: cells },
      doc: Object.assign({}, item.doc, {
        table: Object.assign({}, table, { rows: [] }),
      }),
    });
  });
  return prepared;
}

/**
 * Pure renderer producing a zero-request, standalone HTML document.
 * @param {{id?: string, written_at?: string, title?: string, docs?: Array<{doc: object, diff?: object}>, generated_at?: string}} report
 */
export function renderTableHtml(report) {
  if (!report || typeof report !== "object" || Array.isArray(report)) {
    throw new TypeError("renderTableHtml(report) requires a report object");
  }
  const reportJson = safeScriptJson(prepareStandaloneReport(report));
  const uiJson = safeScriptJson(TABLE_VIEW_UI);
  const runtimeSource = createTableViewRuntime.toString();
  const title = escapeHtml(report.title || TABLE_VIEW_UI.defaultTitle);
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="@omni/table-view-core">
  <title>${title}</title>
  <style>${TABLE_VIEW_CSS}</style>
</head>
<body>
  <div id="table-view-root"></div>
  <script>
    const REPORT = ${reportJson};
    const TABLE_VIEW_UI = ${uiJson};
    const runtime = (${runtimeSource})(window, TABLE_VIEW_UI);
    runtime.mount(document.getElementById("table-view-root"), REPORT);
    const unresolvedToken = ["__", "PLACEHOLDER", "__"].join("");
    if (document.documentElement.innerHTML.includes(unresolvedToken)) throw new Error("Unresolved template placeholder");
  </script>
</body>
</html>`;
  const unresolvedToken = ["__", "PLACEHOLDER", "__"].join("");
  if (html.includes(unresolvedToken)) throw new Error("Unresolved template placeholder");
  return html;
}

export const TABLE_VIEW_LIMITS = Object.freeze({ rows: MAX_ROWS, cells: MAX_CELLS });
