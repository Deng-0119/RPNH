/** Parse launcher selection; original Python HOST still validates every policy. */
export interface ManagedToolSelection { readonly name: string; readonly selector: string }
export interface ManagedBindingsSelection {
  readonly tools: ReadonlyArray<ManagedToolSelection>
  readonly resultReader: boolean
}
const record = (value: unknown): value is Record<string, any> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const exact = (value: unknown, keys: readonly string[]): value is Record<string, unknown> =>
  record(value) && Object.keys(value).length === keys.length
    && Object.keys(value).every(key => keys.includes(key))
const text = (value: unknown, max = 512): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max
    && value.trim() === value && !/[\u0000-\u001f\u007f]/.test(value)

function accountSelection(plan: unknown, provider: unknown, toolNames: readonly string[]): void {
  if (!exact(plan, ['schema_version', 'source_id', 'bindings'])
    || plan.schema_version !== 'rpnh/stdio_account_plan/v1' || !text(plan.source_id)
    || !Array.isArray(plan.bindings) || plan.bindings.length === 0 || plan.bindings.length > 64
    || !exact(provider, ['schema_version', 'entry_point', 'implementation'])
    || provider.schema_version !== 'rpnh/installed_account_provider/v1'
    || !text(provider.entry_point, 128) || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(provider.entry_point)) {
    throw new Error('account bindings require an exact plan and installed provider selection')
  }
  const implementation = provider.implementation
  if (!exact(implementation, ['schema_version', 'implementation_id', 'revision', 'handler'])
    || implementation.schema_version !== 'rpnh/plugin_implementation/v2'
    || !text(implementation.implementation_id, 192)
    || !/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(implementation.implementation_id)
    || !text(implementation.revision, 64) || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(implementation.revision)
    || !text(implementation.handler)
    || !/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(implementation.handler)) {
    throw new Error('invalid installed account provider identity')
  }
  const selected = new Set<string>()
  for (const binding of plan.bindings) {
    if (!exact(binding, ['node_id', 'tool_name', 'service_id', 'account_id', 'binding_revision', 'slot'])
      || binding.node_id !== 'dsh' || !text(binding.tool_name, 48)
      || !toolNames.includes(binding.tool_name) || selected.has(binding.tool_name)
      || !text(binding.service_id) || !text(binding.account_id) || !text(binding.binding_revision)
      || binding.slot !== 'github_pat/v1') {
      throw new Error('account bindings must name exact selected DSH tools')
    }
    selected.add(binding.tool_name)
  }
}
export function validManagedIdentity(name: unknown, selector: unknown): boolean {
  return typeof name === 'string' && typeof selector === 'string'
    && /^[a-z][a-z0-9_]{0,47}$/.test(name)
    && /^[a-z][a-z0-9_]{0,47}\/[a-z][a-z0-9_]{0,47}$/.test(selector)
}
export function managedBindingsDocument(raw: string): ManagedBindingsSelection {
  let document: unknown
  try { document = JSON.parse(raw) } catch { throw new Error('--managed-bindings requires exact JSON') }
  if (!record(document)) throw new Error('--managed-bindings requires an object')
  const accounts = document.schema_version === 'rpnh/dsh_managed_bindings/v3'
  const allowedKeys = new Set(['schema_version', 'tools', 'admitted_effects',
    'managed_tool_policy', 'managed_result_reader', 'managed_result_import_max_bytes',
    ...(accounts ? ['managed_accounts', 'managed_account_provider'] : [])])
  if (Object.keys(document).some(key => !allowedKeys.has(key))
    || !['rpnh/dsh_managed_bindings/v1', 'rpnh/dsh_managed_bindings/v2', 'rpnh/dsh_managed_bindings/v3'].includes(document.schema_version)
    || !record(document.tools) || !Array.isArray(document.admitted_effects)
    || document.admitted_effects.length === 0
    || ('managed_tool_policy' in document && !record(document.managed_tool_policy))
    || ('managed_result_reader' in document && typeof document.managed_result_reader !== 'boolean')) {
    throw new Error('invalid managed bindings document')
  }
  if ('managed_result_import_max_bytes' in document
    && (!Number.isSafeInteger(document.managed_result_import_max_bytes)
      || document.managed_result_import_max_bytes < 0 || document.managed_result_reader !== true)) {
    throw new Error('optional import budget requires reader and nonnegative safe integer')
  }
  // Policy semantics and registration identity remain with the original core.
  // The document path, including the unchanged policy, is passed to Python.
  const allowedEffects = new Set(['pure', 'external_read', 'external_write'])
  if (document.admitted_effects.some((effect: unknown) => typeof effect !== 'string' || !allowedEffects.has(effect))
    || new Set(document.admitted_effects).size !== document.admitted_effects.length) {
    throw new Error('invalid managed admitted effects')
  }
  const tools: ManagedToolSelection[] = []
  const selectors = new Set<string>()
  const resultReader = document.managed_result_reader === true
  const versioned = document.schema_version !== 'rpnh/dsh_managed_bindings/v1'
  for (const [name, binding] of Object.entries(document.tools)) {
    if (!record(binding)
      || (versioned
        ? Object.keys(binding).some(key => !['selector', 'descriptor_revision', 'description', 'input_schema'].includes(key))
          || typeof binding.descriptor_revision !== 'string'
          || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(binding.descriptor_revision)
        : Object.keys(binding).sort().join(',') !== 'description,input_schema,selector')
      || !validManagedIdentity(name, binding.selector)
      || ((!versioned || 'description' in binding)
        && (typeof binding.description !== 'string' || !binding.description.trim()))
      || ((!versioned || 'input_schema' in binding)
        && (!record(binding.input_schema) || binding.input_schema.type !== 'object'))
      || selectors.has(binding.selector)
      || (resultReader && name === 'read_managed_output')) {
      throw new Error('managed bindings require unique names/selectors, descriptions, object schemas and an unambiguous reader')
    }
    selectors.add(binding.selector)
    tools.push(Object.freeze({ name, selector: binding.selector }))
  }
  if (!tools.length) throw new Error('managed bindings require at least one tool')
  if (accounts) accountSelection(document.managed_accounts, document.managed_account_provider,
    tools.map(tool => tool.name))
  return Object.freeze({ tools: Object.freeze(tools), resultReader })
}
export function managedRequestToolNames(tools: ReadonlyArray<ManagedToolSelection>, resultReader = false): string[] {
  const names = tools.map(tool => tool.name)
  if (resultReader) {
    if (names.includes('read_managed_output')) throw new Error('managed result reader name is already selected')
    names.push('read_managed_output')
  }
  return names
}
