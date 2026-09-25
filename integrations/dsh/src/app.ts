/** Managed application using the genuine DSH factory and headless runner. */
import { Context } from '@deepseek-ai/cordis'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import AgentDefaultModelConfig from '@deepseek-ai/dsh-agent-default-model'
import * as headless from '../packages/bundle/headless/src/index.ts'
import { OwnerClient, RegistryBridge, selectedModel, type BridgeConfig, type BridgeProcessConfig,
  type PublicExecutionProfile } from './bridge.ts'
import { CapabilityHost } from './capabilities.ts'
import { RegistryProjectionPersistence } from './projection.ts'
import { RegistryAgent, RegistryAgentLoop } from './agent.ts'
import { readFile } from 'node:fs/promises'
import { isAbsolute, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

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
const usage = `Managed DSH application (experimental; provider execution is supplied by RPNH)
  --offline --root DIR --data-file NUMBERS.json --task TEXT [--deny] [--json]
  --execution PATH --root DIR --task TEXT [--deny] [--json]
    [--plugin-config /ABS/PLUGINS.json --managed-tool NAME=PLUGIN/OPERATION ...]
  --history --root DIR --session-id ID
  (--offline | --execution PATH) --resume --root DIR --session-id ID
History is read from the Registry. Resume uses the stored request, not a new prompt.`
function nonempty(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value || value !== value.trim()) throw new Error(`invalid ${label}`)
  return value
}
function configuredProfile(raw: string): PublicExecutionProfile {
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid launcher execution profile')
  const profile = value as Record<string, unknown>
  if (profile.schema_version !== 'rpnh/dsh_execution_profile/v2') {
    throw new Error('invalid launcher execution profile')
  }
  const adapterKind = String(profile.adapter_kind)
  if (adapterKind !== 'external_provider' && adapterKind !== 'local_process') throw new Error('invalid execution profile adapter_kind')
  const positive = (name: string): number => {
    const value = profile[name]
    if (!Number.isSafeInteger(value) || Number(value) < 1) throw new Error(`invalid execution profile ${name}`)
    return Number(value)
  }
  return Object.freeze({
    schema_version: 'rpnh/dsh_execution_profile/v2',
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
  })
}
function managedToolSelections(raw: string[] | undefined): ReadonlyArray<{ name: string; selector: string }> {
  const output: Array<{ name: string; selector: string }> = []
  const names = new Set<string>(); const selectors = new Set<string>()
  for (const item of raw ?? []) {
    const separator = item.indexOf('=')
    const name = separator < 0 ? '' : item.slice(0, separator)
    const selector = separator < 0 ? '' : item.slice(separator + 1)
    if (!/^[a-z][a-z0-9_]{0,47}$/.test(name)
      || !/^[a-z][a-z0-9_]{0,47}\/[a-z][a-z0-9_]{0,47}$/.test(selector)
      || names.has(name) || selectors.has(selector)) {
      throw new Error('--managed-tool requires unique NAME=PLUGIN/OPERATION lowercase identifiers')
    }
    names.add(name); selectors.add(selector); output.push(Object.freeze({ name, selector }))
  }
  return Object.freeze(output)
}
async function main(): Promise<void> {
  const { values } = parseArgs({ options: {
    offline: { type: 'boolean' }, root: { type: 'string' }, 'data-file': { type: 'string' },
    task: { type: 'string' }, deny: { type: 'boolean' }, json: { type: 'boolean' },
    history: { type: 'boolean' }, resume: { type: 'boolean' }, 'session-id': { type: 'string' },
    python: { type: 'string' }, 'execution-path': { type: 'string' }, 'execution-profile': { type: 'string' },
    'plugin-config': { type: 'string' }, 'managed-tool': { type: 'string', multiple: true },
    help: { type: 'boolean' },
  }, allowPositionals: false })
  if (values.help) { console.log(usage); return }
  if (!values.root || !values.python || (values.history && values.resume)) throw new Error(usage)
  const base = resolve(process.env.RPNH_DSH_CALLER_CWD ?? process.cwd())
  process.chdir(base)
  const common = { root: resolve(base, values.root), python: values.python, data: [], allowRequest: true,
    tools: ['read_dataset', 'sum_values'] }
  if (values.history) {
    if (!values['session-id'] || values.task || values['data-file'] || values.deny
      || values['plugin-config'] || values['managed-tool']) throw new Error(usage)
    console.log(JSON.stringify(await readHistory(common, values['session-id'])))
    return
  }
  const configured = values['execution-path'] !== undefined || values['execution-profile'] !== undefined
  if (Boolean(values.offline) === configured) throw new Error('select exactly one launcher-managed offline or configured profile')
  let config: BridgeConfig
  if (values.offline) config = { ...common, execution: { kind: 'offline' } }
  else {
    const executionPath = nonempty(values['execution-path'], 'execution path')
    if (!isAbsolute(executionPath)) throw new Error('execution path must be absolute')
    const selections = managedToolSelections(values['managed-tool'])
    const pluginConfigPath = values['plugin-config']
    if ((pluginConfigPath === undefined) !== (selections.length === 0)) {
      throw new Error('--plugin-config and at least one --managed-tool are required together')
    }
    if (pluginConfigPath !== undefined && !isAbsolute(pluginConfigPath)) {
      throw new Error('plugin config path must be absolute')
    }
    config = { ...common,
      ...(pluginConfigPath === undefined ? {} : { pluginConfigPath, managedTools: selections }),
      execution: { kind: 'configured', selectionPath: executionPath,
      profile: configuredProfile(nonempty(values['execution-profile'], 'execution profile')) } }
  }
  if (values.resume) {
    if (!values['session-id'] || values.task || values['data-file'] || values.deny) throw new Error(usage)
    const result = await resumeSession(config, values['session-id'])
    console.log(JSON.stringify(result))
    process.exitCode = ['terminal', 'idle'].includes(result.status) ? 0 : 1
    return
  }
  if (!values.task || values['session-id']) throw new Error(usage)
  if (values.offline) {
    if (!values['data-file']) throw new Error(usage)
    const data: unknown = JSON.parse(await readFile(resolve(base, values['data-file']), 'utf8'))
    if (!Array.isArray(data) || data.some(x => typeof x !== 'number' || !Number.isFinite(x))) throw new Error('data-file must contain a finite numeric array')
    config.data = data
  } else {
    if (values['data-file']) throw new Error('configured execution does not accept the offline numeric data-file')
    config.tools = (config.managedTools ?? []).map(tool => tool.name)
  }
  config.allowRequest = !values.deny
  process.exitCode = await runHeadless(config, values.task, values.json)
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  void main().catch(error => { console.error(String(error)); process.exitCode = 1 })
}
