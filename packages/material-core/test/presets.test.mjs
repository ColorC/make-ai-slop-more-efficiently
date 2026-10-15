import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyMaterialPreset,
  resolveMaterialPreset,
  validateMaterialGraph,
  validateMaterialPresetProfile,
  validateMaterialPresetRef,
} from '../index.js'

const classification = {
  schema: 'omni.material-classification-ref/v1',
  profile_id: 'spark-workshop.materials',
  profile_revision: 'v2',
  primary_path: ['visible-form', 'document'],
  facets: { purpose: [['input', 'planning-source']] },
}
const profile = {
  schema: 'omni.material-preset-profile/v1',
  profile_id: 'spark-workshop.material-presets',
  project_id: 'spark-workshop',
  revision: 'v1',
  presets: [{
    id: 'config-planning', label: '规划', definition: '少量但足量的关键输入。',
    is_not: '不是全量配置。',
    source_ref: { kind: 'workspace-file', ref: 'workshop/content/material/presets/config.json#config-planning' },
    classification_ref: classification,
    representation_defaults: { media_type: 'text/markdown', renderer_hint: 'planning-source' },
    display: { badge_label: '规划' },
  }],
}

test('preset profile rejects missing sources and duplicate definitions', () => {
  assert.deepEqual(validateMaterialPresetProfile(profile), [])
  const missing = structuredClone(profile)
  delete missing.presets[0].source_ref
  assert.match(validateMaterialPresetProfile(missing).join('\n'), /source_ref/)
  const duplicate = structuredClone(profile)
  duplicate.presets.push({ ...structuredClone(duplicate.presets[0]), id: 'planning-copy' })
  assert.match(validateMaterialPresetProfile(duplicate).join('\n'), /完全重复/)
})

test('preset ref is versioned and expands classification and representation defaults', () => {
  const presetRef = { profile_id: profile.profile_id, profile_revision: profile.revision, preset_id: 'config-planning' }
  assert.deepEqual(validateMaterialPresetRef(presetRef, profile), [])
  assert.equal(resolveMaterialPreset(presetRef, profile).label, '规划')
  const material = applyMaterialPreset({
    id: 'material:spark-workshop/config/history/planning', title: '规划', labels: ['配置'],
    revision_ref: { provider: 'p4', object: '//streams/main/Design/excel/A.xlsm', revision: '13360' },
    preset_ref: presetRef,
    representations: [{ id: 'primary', role: 'primary', source_ref: { kind: 'p4', ref: '//streams/main/Design/excel/A.xlsm@=13360' } }],
  }, profile)
  assert.equal(material.representations[0].media_type, 'text/markdown')
  assert.deepEqual(material.metadata.classification_ref, classification)
  assert.equal(material.metadata.preset_label, '规划')
  assert.equal(validateMaterialGraph({
    contract_version: '0.3.0', graph_id: 'material-graph:test', generated_at: '2026-08-22T00:00:00Z', materials: [material],
  }).ok, true)
})

test('preset expansion rejects a manually conflicting classification', () => {
  assert.throws(() => applyMaterialPreset({
    id: 'material:spark-workshop/config/history/planning', title: '规划', labels: [],
    revision_ref: { provider: 'p4', object: 'x', revision: '1' },
    preset_ref: { profile_id: profile.profile_id, profile_revision: profile.revision, preset_id: 'config-planning' },
    representations: [{ id: 'primary', role: 'primary', source_ref: { kind: 'p4', ref: 'x@1' } }],
    metadata: { classification_ref: { ...classification, primary_path: ['visible-form', 'table'] } },
  }, profile), /冲突/)
})
