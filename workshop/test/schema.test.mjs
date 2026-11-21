// Contract checks for the published Workshop canvas / card / edge / plugin JSON Schemas.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const schemaDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'shared', 'schema');
const schemas = Object.fromEntries(
    fs.readdirSync(schemaDir)
        .filter((name) => name.endsWith('.schema.json'))
        .map((name) => [name, JSON.parse(fs.readFileSync(path.join(schemaDir, name), 'utf8'))]),
);

function collectRefs(node, out = []) {
    if (Array.isArray(node)) node.forEach((item) => collectRefs(item, out));
    else if (node && typeof node === 'object') {
        for (const [key, value] of Object.entries(node)) {
            if (key === '$ref' && typeof value === 'string') out.push(value);
            else collectRefs(value, out);
        }
    }
    return out;
}

function resolvePointer(doc, pointer) {
    return pointer.split('/').filter(Boolean).reduce((node, part) => (node == null ? undefined : node[part.replace(/~1/g, '/').replace(/~0/g, '~')]), doc);
}

test('the four contract schemas are present, draft 2020-12 and uniquely identified', () => {
    assert.deepEqual(Object.keys(schemas).sort(), ['canvas.schema.json', 'card.schema.json', 'edge.schema.json', 'plugin.schema.json']);
    const ids = Object.values(schemas).map((schema) => schema.$id);
    assert.equal(new Set(ids).size, ids.length);
    for (const schema of Object.values(schemas)) {
        assert.equal(schema.$schema, 'https://json-schema.org/draft/2020-12/schema');
        assert.equal(typeof schema.title, 'string');
    }
});

test('every $ref resolves inside the published schema set', () => {
    const byId = new Map(Object.values(schemas).map((schema) => [schema.$id, schema]));
    for (const [name, schema] of Object.entries(schemas)) {
        for (const ref of collectRefs(schema)) {
            const [base, fragment = ''] = ref.split('#');
            const target = base ? byId.get(base) : schema;
            assert.ok(target, `${name}: unresolved schema ${ref}`);
            if (fragment) assert.notEqual(resolvePointer(target, fragment), undefined, `${name}: unresolved pointer ${ref}`);
        }
    }
});
