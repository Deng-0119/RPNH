/** Managed application using the genuine DSH factory and headless runner. */
import { Context } from '@deepseek-ai/cordis'
import LlmRuntime, { createUserMessage } from '@deepseek-ai/dsh-llm'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import AgentDefaultModelConfig from '@deepseek-ai/dsh-agent-default-model'
import * as headless from '../packages/bundle/headless/src/index.ts'
import { OwnerClient, RegistryBridge, selectedModel, type BridgeConfig, type BridgeProcessConfig,
  type ManagedToolSelection, type PublicExecutionProfile } from './bridge.ts'
import { CapabilityHost } from './capabilities.ts'
import { RegistryProjectionPersistence } from './projection.ts'
import { RegistryAgent, RegistryAgentLoop } from './agent.ts'
import { readFile } from 'node:fs/promises'
import { isAbsolute, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import { managedBindingsDocument, managedRequestToolNames, validManagedIdentity } from './managed-bindings-codec.ts'

export async function createApplication(config: BridgeConfig): Promise<Context> {
  const ctx = new Context()
  try {
    const model = selectedModel(config)
    await ctx.plugin(LlmRuntime)
    await ctx.plugin(SessionStore)
    await ctx.plugin(SessionProjectionRegistry)
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(AgentRegistry)
    await ctx.plugin(AgentDefaultModelConfig, model)
    await ctx.plugin(RegistryBridge, config)
    await ctx.plugin(CapabilityHost)
    await ctx.plugin(RegistryProjectionPersistence)
    await ctx.plugin(RegistryAgentLoop, { agents: [] })
    return ctx
  } catch (error) { await ctx.fiber.dispose(); throw error }
}
export async function runHeadless(config: BridgeConfig, task: string, json = false): Promise<number> {
  const ctx = await createApplication(config)
  const done = new Promise<number>(resolveExit => ctx.provide('appExit', resolveExit))
  ctx.on('agent/created', ({ agent }) => { console.error(`RPNH session: ${agent.id}`) })
  try { await ctx.plugin(headless, { task, json }); return await done }
  finally { await ctx.fiber.dispose() }
}
export async function readHistory(config: BridgeProcessConfig, id: string): Promise<unknown> {
  // No Agent, LlmRuntime, ToolRuntime or effect host is constructed by a read.
  const owner = new OwnerClient(id, config, false)
  try { return await owner.request('history') }
  finally { await owner.close() }
}
export async function resumeSession(config: BridgeConfig, id: string): Promise<Record<string, any>> {
  const ctx = await createApplication(config)
  try {
    const model = selectedModel(config)
    const handle = await ctx.agents.resume({ resumeSessionId: SessionId(id),
      agentOptions: model })
    const agent = handle.agent as RegistryAgent
    const result = await agent.resumeActive()
    await agent.whenIdle()
    if (result.status === 'terminal' || result.status === 'idle') await ctx.sessions.flush(agent.session)
    await handle.dispose()
    return result
  } finally { await ctx.fiber.dispose() }
}
/** Submit a new turn to an idle existing session; never infer active recovery. */
export async function followupSession(config: BridgeConfig, id: string, task: string): Promise<Record<string, any>> {
  const ctx = await createApplication(config)
  try {
    const handle = await ctx.agents.resume({ resumeSessionId: SessionId(id), agentOptions: selectedModel(config) })
    try {
      const agent = handle.agent as RegistryAgent
      agent.followup(createUserMessage({ source: { kind: 'user' }, content: [{ type: 'text', text: task }] }))
      await agent.whenIdle()
      const result = agent.lastOutcome
      if (!result) throw new Error('followup completed without an observed Registry outcome')
      if (result.status === 'terminal') await ctx.sessions.flush(agent.session)
      return result
    } finally { await handle.dispose() }
  } finally { await ctx.fiber.dispose() }
}
const usage = `Managed DSH application (experimental; provider execution is supplied by RPNH)
  --offline --root DIR --data-file NUMBERS.json --task TEXT [--deny] [--json]
  --execution-path /ABS/SELECTION.json --execution-profile PROFILE_JSON --root DIR --task TEXT [--deny] [--json]
    [--plugin-config /ABS/PLUGINS.json --managed-tool NAME=PLUGIN/OPERATION ...]
    [--plugin-config /ABS/PLUGINS.json --managed-bindings /ABS/BINDINGS.json]
    [--attempt-budget POSITIVE_INTEGER|unmetered]
  --execution-path /ABS/SELECTION.json --execution-profile PROFILE_JSON --followup --root DIR --session-id ID --task TEXT
  --history --root DIR --session-id ID
  (--offline | --execution-path /ABS/SELECTION.json --execution-profile PROFILE_JSON) --resume --root DIR --session-id ID
All modes require --python PYTHON. Followup requires configured execution and an idle existing session.
History is read from the Registry. Resume uses the stored request, not a new prompt.`
function nonempty(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value || value !== value.trim()) throw new Error(`invalid ${label}`)
  return value
}
function configuredProfile(raw: string): PublicExecutionProfile {
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid launcher execution profile')
  const profile = value as Record<string, unknown>
  if (profile.schema_version !== 'rpnh/dsh_execution_profile/v2'
    && profile.schema_version !== 'rpnh/dsh_execution_profile/v3') {
    throw new Error('invalid launcher execution profile')
  }
  const adapterKind = String(profile.adapter_kind)
  if (adapterKind !== 'external_provider' && adapterKind !== 'local_process') throw new Error('invalid execution profile adapter_kind')
  const positive = (name: string): number => {
    const value = profile[name]
    if (!Number.isSafeInteger(value) || Number(value) < 1) throw new Error(`invalid execution profile ${name}`)
    return Number(value)
  }
  const common = {
    schema_version: profile.schema_version,
    profile: nonempty(profile.profile, 'execution profile profile'),
    selection_id: nonempty(profile.selection_id, 'execution profile selection_id'),
    provider: nonempty(profile.provider, 'execution profile provider'),
    provider_display_name: nonempty(profile.provider_display_name, 'execution profile provider_display_name'),
    model_condition: nonempty(profile.model_condition, 'execution profile model_condition'),
    adapter_kind: adapterKind,
    transport_kind: nonempty(profile.transport_kind, 'execution profile transport_kind'),
    timeout_seconds: positive('timeout_seconds'),
    max_output_tokens: positive('max_output_tokens'),
    max_response_bytes: positive('max_response_bytes'),
  } as const
  if (profile.schema_version === 'rpnh/dsh_execution_profile/v2') {
    return Object.freeze(common)
  }
  const selected = profile.reasoning_effort
  const supported = profile.supported_reasoning_efforts
  const fallback = profile.default_reasoning_effort
  if ((selected !== null && typeof selected !== 'string')
    || !Array.isArray(supported)
    || supported.some(item => typeof item !== 'string' || item.length === 0)
    || new Set(supported).size !== supported.length
    || (fallback !== null && typeof fallback !== 'string')
    || (selected !== null && !supported.includes(selected))
    || (fallback !== null && !supported.includes(fallback))) {
    throw new Error('invalid execution profile reasoning effort')
  }
  return Object.freeze({ ...common,
    reasoning_effort: selected as string | null,
    supported_reasoning_efforts: Object.freeze([...(supported as string[])]),
    default_reasoning_effort: fallback as string | null,
  })
}
function managedToolSelections(raw: string[] | undefined): ReadonlyArray<ManagedToolSelection> {
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
function attemptBudget(raw: string | undefined): number | null | undefined {
  if (raw === undefined) return undefined
  if (raw === 'unmetered') return null
  const value = Number(raw)
  if (!Number.isSafeInteger(value) || value < 1 || String(value) !== raw) {
    throw new Error('--attempt-budget requires a positive integer or unmetered')
  }
  return value
}
async function main(): Promise<void> {
  const { values } = parseArgs({ options: {
    offline: { type: 'boolean' }, root: { type: 'string' }, 'data-file': { type: 'string' },
    task: { type: 'string' }, deny: { type: 'boolean' }, json: { type: 'boolean' },
    history: { type: 'boolean' }, resume: { type: 'boolean' }, followup: { type: 'boolean' }, 'session-id': { type: 'string' },
    python: { type: 'string' }, 'execution-path': { type: 'string' }, 'execution-profile': { type: 'string' },
    'plugin-config': { type: 'string' }, 'managed-tool': { type: 'string', multiple: true },
    'managed-bindings': { type: 'string' },
    'attempt-budget': { type: 'string' },
    help: { type: 'boolean' },
  }, allowPositionals: false })
  if (values.help) { console.log(usage); return }
  if (!values.root || !values.python || ([values.history, values.resume, values.followup].filter(Boolean).length > 1)) throw new Error(usage)
  const base = resolve(process.env.RPNH_DSH_CALLER_CWD ?? process.cwd())
  process.chdir(base)
  const common = { root: resolve(base, values.root), python: values.python, data: [], allowRequest: true,
    tools: ['read_dataset', 'sum_values'] }
  if (values.history) {
    if (!values['session-id'] || values.task || values['data-file'] || values.deny
      || values['plugin-config'] || values['managed-tool'] || values['managed-bindings']
      || values['attempt-budget']) throw new Error(usage)
    console.log(JSON.stringify(await readHistory(common, values['session-id'])))
    return
  }
  const configured = values['execution-path'] !== undefined || values['execution-profile'] !== undefined
  if (Boolean(values.offline) === configured) throw new Error('select exactly one launcher-managed offline or configured profile')
  if (values['managed-tool'] && values['managed-bindings']) {
    throw new Error('--managed-tool and --managed-bindings are mutually exclusive')
  }
  if (values.offline && (values['plugin-config'] || values['managed-tool']
    || values['managed-bindings'])) {
    throw new Error('offline execution does not accept managed provider tools')
  }
  let config: BridgeConfig
  let resultReader = false
  if (values.offline) config = { ...common, execution: { kind: 'offline' } }
  else {
    const executionPath = nonempty(values['execution-path'], 'execution path')
    if (!isAbsolute(executionPath)) throw new Error('execution path must be absolute')
    const managedBindingsPath = values['managed-bindings']
    if (managedBindingsPath !== undefined && !isAbsolute(managedBindingsPath)) {
      throw new Error('managed bindings path must be absolute')
    }
    const bindings = managedBindingsPath === undefined ? undefined
      : managedBindingsDocument(await readFile(managedBindingsPath, 'utf8'))
    const selections = bindings?.tools ?? managedToolSelections(values['managed-tool'])
    resultReader = bindings?.resultReader ?? false
    const pluginConfigPath = values['plugin-config']
    if ((pluginConfigPath === undefined) !== (selections.length === 0)) {
      throw new Error('--plugin-config and managed tools are required together')
    }
    if (pluginConfigPath !== undefined && !isAbsolute(pluginConfigPath)) {
      throw new Error('plugin config path must be absolute')
    }
    config = { ...common,
      ...(pluginConfigPath === undefined ? {} : { pluginConfigPath, managedTools: selections,
        ...(managedBindingsPath === undefined ? {} : { managedBindingsPath }) }),
      execution: { kind: 'configured', selectionPath: executionPath,
      profile: configuredProfile(nonempty(values['execution-profile'], 'execution profile')) } }
  }
  if (values.resume) {
    if (!values['session-id'] || values.task || values['data-file'] || values.deny
      || values['attempt-budget']) throw new Error(usage)
    const result = await resumeSession(config, values['session-id'])
    console.log(JSON.stringify(result))
    process.exitCode = ['terminal', 'idle'].includes(result.status) ? 0 : 1
    return
  }
  if (!values.task || (values.followup ? (!values['session-id'] || values.offline || values['attempt-budget'] !== undefined) : values['session-id'])) throw new Error(usage)
  const budget = attemptBudget(values['attempt-budget'])
  if (budget !== undefined) config.attemptBudget = budget
  if (values.offline) {
    if (!values['data-file']) throw new Error(usage)
    const data: unknown = JSON.parse(await readFile(resolve(base, values['data-file']), 'utf8'))
    if (!Array.isArray(data) || data.some(x => typeof x !== 'number' || !Number.isFinite(x))) throw new Error('data-file must contain a finite numeric array')
    config.data = data
  } else {
    if (values['data-file']) throw new Error('configured execution does not accept the offline numeric data-file')
    config.tools = managedRequestToolNames(config.managedTools ?? [], resultReader)
  }
  config.allowRequest = !values.deny
  if (values.followup) {
    const result = await followupSession(config, values['session-id']!, values.task)
    console.log(JSON.stringify(result))
    process.exitCode = result.status === 'terminal' ? 0 : 1
  } else process.exitCode = await runHeadless(config, values.task, values.json)
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  void main().catch(error => { console.error(String(error)); process.exitCode = 1 })
}
