/** CLI managed-tool name projection; scheduling authority remains in Python. */
import type { ManagedToolSelection } from './bridge.ts'

function validManagedIdentity(name: unknown, selector: unknown): boolean {
  return typeof name === 'string' && typeof selector === 'string'
    && /^[a-z][a-z0-9_]{0,47}$/.test(name)
    && /^[a-z][a-z0-9_]{0,47}\/[a-z][a-z0-9_]{0,47}$/.test(selector)
}
export function managedToolSelections(raw: string[] | undefined): ReadonlyArray<ManagedToolSelection> {
  const output: ManagedToolSelection[] = []
  const names = new Set<string>(); const selectors = new Set<string>()
  for (const item of raw ?? []) {
    const separator = item.indexOf('=')
    const name = separator < 0 ? '' : item.slice(0, separator)
    const selector = separator < 0 ? '' : item.slice(separator + 1)
    if (!validManagedIdentity(name, selector)
      || names.has(name) || selectors.has(selector)) {
      throw new Error('--managed-tool requires unique NAME=PLUGIN/OPERATION lowercase identifiers')
    }
    names.add(name); selectors.add(selector); output.push(Object.freeze({ name, selector }))
  }
  return Object.freeze(output)
}
export function managedBindingsDocument(raw: string): ReadonlyArray<ManagedToolSelection> {
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('--managed-bindings requires exact JSON') }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('--managed-bindings requires an object')
  }
  const document = value as Record<string, unknown>
  // The launcher projects visible names only. The original file path reaches
  // Python, where the existing core validates and registers the HOST policy.
  // Keep policy contents opaque here so the frontend cannot infer new authority.
  const fields = Object.keys(document).sort().join(',')
  if (!['admitted_effects,schema_version,tools',
    'admitted_effects,managed_tool_policy,schema_version,tools'].includes(fields)
    || document.schema_version !== 'rpnh/dsh_managed_bindings/v1'
    || !document.tools || typeof document.tools !== 'object' || Array.isArray(document.tools)
    || !Array.isArray(document.admitted_effects) || document.admitted_effects.length === 0) {
    throw new Error('invalid managed bindings document')
  }
  const effects = document.admitted_effects as unknown[]
  const allowed = new Set(['pure', 'external_read', 'external_write'])
  if (effects.some(effect => typeof effect !== 'string' || !allowed.has(effect))
    || new Set(effects).size !== effects.length) {
    throw new Error('invalid managed admitted effects')
  }
  const output: ManagedToolSelection[] = []
  const names = new Set<string>(); const selectors = new Set<string>()
  for (const [name, selected] of Object.entries(document.tools as Record<string, unknown>)) {
    if (!selected || typeof selected !== 'object' || Array.isArray(selected)) {
      throw new Error('invalid managed tool binding')
    }
    const binding = selected as Record<string, unknown>
    const keys = Object.keys(binding).sort()
    const input = binding.input_schema
    if (keys.join(',') !== 'description,input_schema,selector'
      || !validManagedIdentity(name, binding.selector)
      || typeof binding.description !== 'string' || !binding.description.trim()
      || !input || typeof input !== 'object' || Array.isArray(input)
      || (input as Record<string, unknown>).type !== 'object'
      || names.has(name) || selectors.has(binding.selector as string)) {
      throw new Error('managed bindings require unique names/selectors, descriptions, and object input schemas')
    }
    const selector = binding.selector as string
    names.add(name); selectors.add(selector)
    output.push(Object.freeze({ name, selector }))
  }
  if (output.length === 0) throw new Error('managed bindings require at least one tool')
  return Object.freeze(output)
}
