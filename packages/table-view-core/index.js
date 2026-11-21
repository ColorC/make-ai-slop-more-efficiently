/** @omni/table-view-core 公共入口。契约见 contract/*.schema.json 与计划
 *  omnicompany/docs/plans/_cross/[2026-08-18]TABLE-VIEW-FACILITY/plan.md §3.1。 */

export { equals, keyString, normalizeValue, defaultTypeSystem } from "./typesys.js";
export { canonicalJson, sha256Hex, contractHash, contextFingerprint } from "./canonical.js";
export {
  DiffError,
  tableOf,
  normalizeCell,
  classifyCellChange,
  semanticDiff,
  isEmptyDiff,
  diffsExactlyEqual,
} from "./diff.js";
export { ChangeSetError, normalizeChangeSet, applyChangeSet, expectedDiff } from "./changeset.js";
export { verifyRoundTrip } from "./roundtrip.js";
export { isRectangularClipboardText, parseClipboardTsv } from "./clipboard-grid.js";

export const TABLE_DOC_SCHEMA = "omni.table-doc/v1";
export const TABLE_CHANGESET_SCHEMA = "omni.table-changeset/v1";
export const TABLE_DIFF_SCHEMA = "omni.table-diff/v1";
