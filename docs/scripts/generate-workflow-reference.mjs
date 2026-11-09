// Generates generated/reference.json, the input/secret/output tables the docs
// render with <WorkflowReference>, from the reusable workflows' `workflow_call`
// triggers and the composite actions' metadata. Run before `next dev` and
// `next build`, so the tables always match the YAML.

import { mkdirSync, readFileSync, readdirSync, writeFileSync, existsSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'

const docsDir = join(dirname(fileURLToPath(import.meta.url)), '..')
const githubDir = join(docsDir, '..', '.github')
const outFile = join(docsDir, 'generated', 'reference.json')

const text = (value) => (value == null ? '' : String(value).replace(/\s+/g, ' ').trim())

function entries(map, describe) {
  return Object.entries(map ?? {}).map(([name, spec]) => ({ name, ...describe(spec ?? {}) }))
}

function workflowReference(file) {
  const doc = parse(readFileSync(file, 'utf8'))
  const call = doc?.on?.workflow_call
  if (call === undefined) return null
  return {
    name: doc.name ?? basename(file),
    inputs: entries(call?.inputs, (spec) => ({
      type: spec.type ?? 'string',
      required: Boolean(spec.required),
      default: spec.default,
      description: text(spec.description),
    })),
    secrets: entries(call?.secrets, (spec) => ({
      required: Boolean(spec.required),
      description: text(spec.description),
    })),
    outputs: entries(call?.outputs, (spec) => ({ description: text(spec.description) })),
  }
}

function actionReference(file) {
  const doc = parse(readFileSync(file, 'utf8'))
  return {
    name: doc.name,
    description: text(doc.description),
    inputs: entries(doc.inputs, (spec) => ({
      type: 'string',
      required: Boolean(spec.required),
      default: spec.default,
      description: text(spec.description),
    })),
    secrets: [],
    outputs: entries(doc.outputs, (spec) => ({ description: text(spec.description) })),
  }
}

const reference = {}

for (const file of readdirSync(join(githubDir, 'workflows')).sort()) {
  if (!/\.ya?ml$/.test(file)) continue
  const workflow = workflowReference(join(githubDir, 'workflows', file))
  if (workflow) reference[`workflows/${file.replace(/\.ya?ml$/, '')}`] = workflow
}

for (const action of readdirSync(join(githubDir, 'actions')).sort()) {
  const file = ['action.yml', 'action.yaml']
    .map((name) => join(githubDir, 'actions', action, name))
    .find(existsSync)
  if (file) reference[`actions/${action}`] = actionReference(file)
}

mkdirSync(dirname(outFile), { recursive: true })
writeFileSync(outFile, `${JSON.stringify(reference, null, 2)}\n`)
console.log(`Wrote ${Object.keys(reference).length} references to ${outFile}`)
