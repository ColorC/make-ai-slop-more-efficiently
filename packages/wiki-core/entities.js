/** Browser-safe lookup helpers for entities emitted by a wiki vault index. */

function clean(value) {
  return String(value ?? "").trim();
}

function entityList(index) {
  return Array.isArray(index?.entities) ? index.entities.filter((item) => item && typeof item === "object") : [];
}

function fieldIdentity(ref) {
  const match = /^value:[^/]+\/([^/]+)\/(.+)$/.exec(clean(ref));
  return match ? { table: match[1], field: match[2] } : null;
}

function tableIdentity(ref) {
  const match = /^table:[^/]+\/(.+)$/.exec(clean(ref));
  return match ? match[1] : null;
}

function normalizeTableName(value) {
  const path = clean(value).replace(/\\/g, "/").replace(/[#?\s].*$/, "");
  const basename = path.slice(path.lastIndexOf("/") + 1);
  return basename.replace(/\.(?:json|xlsm?|csv|tsv)$/i, "");
}

function setUnique(map, key, entity) {
  const token = clean(key);
  if (!token) return;
  const current = map.get(token);
  if (current && current.id !== entity.id) map.set(token, null);
  else if (current !== null) map.set(token, entity);
}

export function createEntityLookup(index) {
  const entities = entityList(index);
  const byRef = new Map();
  const fields = new Map();
  const tables = new Map();
  const qualified = new Map();
  for (const entity of entities) {
    if (entity.ref) byRef.set(entity.ref, entity);
    const field = fieldIdentity(entity.ref);
    if (field) {
      fields.set(`${field.table}\u0000${field.field}`, entity);
      setUnique(qualified, `${field.table}.${field.field}`, entity);
      for (const token of [...(entity.aliases || []), ...(entity.developerNames || [])]) {
        if (String(token).includes(".")) setUnique(qualified, token, entity);
      }
      continue;
    }
    const table = tableIdentity(entity.ref);
    if (table) {
      tables.set(table, entity);
      for (const token of [table, ...(entity.aliases || []), ...(entity.developerNames || [])]) {
        if (/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(clean(token))) setUnique(tables, token, entity);
      }
    }
  }
  return {
    entities,
    byRef,
    fields,
    tables,
    qualified: new Map([...qualified].filter(([, entity]) => entity)),
  };
}

export function findFieldEntity(lookup, table, field) {
  const tableName = normalizeTableName(table);
  const rawField = clean(field);
  const fieldName = rawField.includes(".") ? rawField.slice(rawField.lastIndexOf(".") + 1) : rawField;
  const tableEntity = lookup?.tables?.get(tableName);
  const canonicalTable = tableIdentity(tableEntity?.ref) || tableName;
  return lookup?.fields?.get(`${canonicalTable}\u0000${fieldName}`) || null;
}

export function findTableEntity(lookup, table) {
  return lookup?.tables?.get(normalizeTableName(table)) || null;
}

export function qualifiedEntityTokens(lookup) {
  return [...(lookup?.qualified?.entries?.() || [])]
    .sort(([left], [right]) => right.length - left.length);
}

export function entityDocumentHref(entity, options = {}) {
  const target = clean(entity?.target);
  if (!target) return "";
  const [rawPath, rawHash = ""] = target.split("#", 2);
  if (!rawPath.toLowerCase().endsWith(".md")) return target;
  const projectId = clean(options.projectId) || "spark-workshop";
  const basePath = clean(options.basePath) || "/materials";
  const path = rawPath.replace(/^\/+/, "").replace(/\.md$/i, "");
  const materialId = `view:${projectId}/doc/${encodeURIComponent(path)}`;
  return `${basePath}?id=${encodeURIComponent(materialId)}${rawHash ? `#${rawHash}` : ""}`;
}
