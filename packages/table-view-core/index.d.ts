/** Cell 四值模型：v=投影/导出值, sv=源侧原始值, f=公式原文, fv=公式缓存值。 */
export interface TableCell {
  v?: unknown;
  sv?: unknown;
  f?: string | null;
  fv?: unknown;
}

export interface TableColumn {
  name: string;
  label?: string;
  type?: string;
  key?: boolean;
}

export interface HeaderLayout {
  rows?: number;
  roles?: string[];
  data_start?: number;
}

export interface TableRow {
  cells: Record<string, TableCell | unknown>;
}

export interface TableModel {
  name: string;
  header_layout?: HeaderLayout;
  columns: TableColumn[];
  key_fields: string[];
  rows: TableRow[];
}

export interface TableSourceLink {
  kind?: string;
  label: string;
  value: string;
}

export interface TableDoc {
  schema: "omni.table-doc/v1";
  source: {
    adapter: string;
    locator: string;
    fingerprint?: { sha256?: string; revision?: string };
    capabilities?: string[];
    /** 表格标题第二行：基于哪个版本（P4 版本 / changelist / 本地文件）；有它时单表也显示标题栏。 */
    basis?: string;
    /** 表格标题右键菜单：逐项复制（本机路径、P4 路径、changelist 号…）。 */
    links?: TableSourceLink[];
  };
  table: TableModel;
}

export type Crud = "Create" | "Update" | "Delete";
export type DeleteScope = "cell" | "row" | "column";
export type ChangeKind = "value" | "formula" | "formula-result" | "source";

export interface ChangeOperation {
  crud: Crud;
  key?: Record<string, unknown>;
  field?: string;
  cells?: Record<string, TableCell | unknown>;
  target?: unknown;
  target_source?: unknown;
  target_projection?: unknown;
  target_formula?: string | null;
  delete_scope?: DeleteScope;
  note?: string;
  /** 以下由 normalizeChangeSet 覆写，调用方写入无效。 */
  current?: unknown;
  current_source?: unknown;
  current_formula?: string | null;
}

export interface TableChangeSet {
  schema?: "omni.table-changeset/v1";
  operations: ChangeOperation[];
}

export interface CellChange {
  before: Required<TableCell>;
  after: Required<TableCell>;
  kind: ChangeKind;
}

export interface TableDiff {
  schema: "omni.table-diff/v1";
  key_fields: string[];
  created: Array<{ key: Record<string, unknown>; after: Record<string, TableCell> }>;
  updated: Array<{ key: Record<string, unknown>; changes: Record<string, CellChange> }>;
  deleted: Array<{ key: Record<string, unknown>; before: Record<string, TableCell> }>;
  counts: { created: number; updated: number; deleted: number };
}

export interface TypeSystem {
  equals(a: unknown, b: unknown, type?: string): boolean;
  keyString(value: unknown): string;
  isKey(column: TableColumn): boolean;
}

export interface DiffOptions {
  typesys?: TypeSystem;
}

export declare class DiffError extends Error {}
export declare class ChangeSetError extends Error {}

export declare function equals(a: unknown, b: unknown, type?: string): boolean;
export declare function keyString(value: unknown): string;
export declare function normalizeValue<T>(value: T): T | null;
export declare const defaultTypeSystem: TypeSystem;

export declare function canonicalJson(value: unknown): string;
export declare function sha256Hex(data: string | Uint8Array): Promise<string>;
export declare function contractHash(payload: object): Promise<string>;
export declare function contextFingerprint(structure: object, sourceBytesSha256: string): Promise<string>;

export declare function tableOf(docOrTable: TableDoc | TableModel): TableModel;
export declare function normalizeCell(cell: unknown): Required<TableCell>;
export declare function classifyCellChange(
  beforeCell: unknown,
  afterCell: unknown,
  type?: string,
  typesys?: TypeSystem,
): CellChange | null;
export declare function semanticDiff(
  beforeDoc: TableDoc | TableModel,
  afterDoc: TableDoc | TableModel,
  opts?: DiffOptions,
): TableDiff;
export declare function isEmptyDiff(diff: TableDiff): boolean;
export declare function diffsExactlyEqual(
  expected: TableDiff,
  actual: TableDiff,
  typesys?: TypeSystem,
): { ok: boolean; reason?: string };

export declare function normalizeChangeSet(
  doc: TableDoc | TableModel,
  changeset: TableChangeSet,
  opts?: DiffOptions,
): { ops: ChangeOperation[] };
export declare function applyChangeSet(
  doc: TableDoc | TableModel,
  changeset: TableChangeSet,
  opts?: DiffOptions,
): TableDoc | TableModel;
export declare function expectedDiff(
  doc: TableDoc | TableModel,
  changeset: TableChangeSet,
  opts?: DiffOptions,
): TableDiff;

export interface RoundTripPlugin {
  read(source: unknown): Promise<TableDoc> | TableDoc;
  write(doc: TableDoc): Promise<unknown> | unknown;
}
export declare function verifyRoundTrip(
  plugin: RoundTripPlugin,
  source: unknown,
  opts?: DiffOptions,
): Promise<{ ok: boolean; diff: TableDiff; doc: TableDoc; rereadDoc: TableDoc }>;

export declare function isRectangularClipboardText(text: string): boolean;
export declare function parseClipboardTsv(text: string): string[][];

export declare const TABLE_DOC_SCHEMA: "omni.table-doc/v1";
export declare const TABLE_CHANGESET_SCHEMA: "omni.table-changeset/v1";
export declare const TABLE_DIFF_SCHEMA: "omni.table-diff/v1";
