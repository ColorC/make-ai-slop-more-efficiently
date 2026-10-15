#!/usr/bin/env node
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { lintCanvasDocument, lintMaterialGraph } from './index.js'

const args = process.argv.slice(2)
let mode = 'auto'
let requireFormatTag = true
let rendererFile = null
const files = []
for (let index = 0; index < args.length; index += 1) {
  const arg = args[index]
  if (arg === '--canvas') mode = 'canvas'
  else if (arg === '--graph') mode = 'graph'
  else if (arg === '--compat') requireFormatTag = false
  else if (arg === '--renderers') rendererFile = args[++index]
  else files.push(arg)
}
if (!files.length) {
  console.error('usage: material-lint [--graph|--canvas] [--compat] [--renderers renderers.json] <file.json> [...]')
  process.exit(2)
}

let renderers
if (rendererFile) {
  const value = JSON.parse(readFileSync(resolve(rendererFile), 'utf8'))
  renderers = Array.isArray(value) ? value : value.renderers
  if (!Array.isArray(renderers)) throw new TypeError('--renderers must contain an array or { renderers: [] }')
}

let failed = false
const results = files.map((file) => {
  const value = JSON.parse(readFileSync(resolve(file), 'utf8'))
  const selected = mode === 'auto'
    ? (Array.isArray(value?.materials) ? 'graph' : Array.isArray(value?.cards) ? 'canvas' : null)
    : mode
  if (!selected) throw new TypeError(`${file}: cannot infer Material Graph or Canvas document`)
  const result = selected === 'canvas'
    ? lintCanvasDocument(value, { requireFormatTag, requireClassification: requireFormatTag, renderers })
    : lintMaterialGraph(value, { requireFormatTag, requireClassification: requireFormatTag, renderers })
  if (!result.ok) failed = true
  return { file, kind: selected, ...result }
})
console.log(JSON.stringify({ ok: !failed, results }, null, 2))
if (failed) process.exitCode = 1
