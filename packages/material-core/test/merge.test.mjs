import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeMaterialPayloads } from '../index.js'

const revision = (revision) => ({ provider: 'p4', object: '//spark/Design/excel/A.xlsm', revision })

test('merge keeps exact revisions, field provenance and display recipe', () => {
  const merged = mergeMaterialPayloads([
    { source_id: 'template', revision_ref: revision('13359'), data: { duration: 10, rows: [{ id: 1, value: 8 }] } },
    { source_id: 'planning', revision_ref: revision('13360'), data: { duration: 12, rows: [{ id: 2, value: 9 }] } },
  ], {
    fields: {
      duration: { strategy: 'prefer-source', source_id: 'planning' },
      rows: { strategy: 'keyed-table', key: 'id' },
    },
    display: { mode: 'table', columns: ['id', 'value'], provenance: 'per-field' },
  })
  assert.equal(merged.data.duration, 12)
  assert.deepEqual(merged.data.rows.map((row) => row.id), [1, 2])
  assert.equal(merged.provenance.duration.sources[0], 'planning')
  assert.equal(merged.source_revisions.template.revision, '13359')
  assert.equal(merged.display.provenance, 'per-field')
})

test('merge rejects implicit conflicts and imprecise sources', () => {
  assert.throws(() => mergeMaterialPayloads([
    { source_id: 'a', revision_ref: revision('1'), data: { value: 1 } },
    { source_id: 'b', revision_ref: revision('2'), data: { value: 2 } },
  ], { fields: { value: {} } }), /必须声明合并策略/)
  assert.throws(() => mergeMaterialPayloads([
    { source_id: 'a', revision_ref: { provider: 'p4' }, data: {} },
    { source_id: 'b', revision_ref: revision('2'), data: {} },
  ], { fields: {} }), /精确版本引用/)
})
