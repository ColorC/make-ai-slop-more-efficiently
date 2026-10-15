export interface WikiEntity {
  id: string;
  kind: string;
  label: string;
  ref: string;
  description?: string;
  target?: string;
  source?: string;
  version?: string;
  aliases?: string[];
  developerNames?: string[];
}

export interface WikiEntityLookup {
  entities: WikiEntity[];
  byRef: Map<string, WikiEntity>;
  fields: Map<string, WikiEntity>;
  tables: Map<string, WikiEntity>;
  qualified: Map<string, WikiEntity>;
}

export function createEntityLookup(index: { entities?: WikiEntity[] }): WikiEntityLookup;
export function findFieldEntity(lookup: WikiEntityLookup | null | undefined, table: unknown, field: unknown): WikiEntity | null;
export function findTableEntity(lookup: WikiEntityLookup | null | undefined, table: unknown): WikiEntity | null;
export function qualifiedEntityTokens(lookup: WikiEntityLookup | null | undefined): Array<[string, WikiEntity]>;
export function entityDocumentHref(entity: WikiEntity | null | undefined, options?: { projectId?: string; basePath?: string }): string;
