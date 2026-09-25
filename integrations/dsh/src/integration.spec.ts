/** Genuine DSH runtime, factory, tools, sessions and headless application. */
import { describe, it, expect } from 'vitest'
import { mkdtemp, rm, writeFile, readdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { execFileSync } from 'node:child_process'
import { SessionId } from '@deepseek-ai/dsh-session'
import { ToolCallId, createUserMessage } from '@deepseek-ai/dsh-llm'
import { createApplication } from './app.ts'
import { RegistryAgent } from './agent.ts'
import { selectedModel, type BridgeConfig } from './bridge.ts'

const options = { provider: 'rpnh-offline', model: 'deterministic-v1' }
const python = execFileSync('sh', ['-c', 'command -v python3']).toString().trim()
const offline = { execution: { kind: 'offline' as const } }
const message = (text: string) => createUserMessage({ source: { kind: 'user' }, content: [{ type: 'text', text }] })
async function fixture(allowRequest = true, tools = ['read_dataset', 'sum_values']) {
  const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-'))
  const config = { root, python, data: [2,3,7], allowRequest, tools, ...offline }
  const ctx = await createApplication(config)
  return { root, config, ctx, cleanup: async () => { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) } }
}
async function agent(ctx: Awaited<ReturnType<typeof createApplication>>, id: string) {
  return await ctx.agents.create({ sessionId: SessionId(id), agentOptions: options, meta: { cwd: process.cwd() } })
}
const registeredTools = [{ type: 'function', function: { name: 'managed_sum', description: 'Sum registered values',
  parameters: { type: 'object', additionalProperties: false, properties: { values: { type: 'array', items: { type: 'number' } } }, required: ['values'] } } }]
const configuredProfile: BridgeConfig['execution'] = { kind: 'configured', selectionPath: '/fixed/selection.json', profile: {
  schema_version: 'rpnh/dsh_execution_profile/v2', profile: 'fixed-frame', selection_id: 'fixed/frame-model',
  provider: 'fixed', provider_display_name: 'Fixed', model_condition: 'frame-model', adapter_kind: 'local_process',
  transport_kind: 'subprocess', timeout_seconds: 30, max_output_tokens: 64, max_response_bytes: 65536,
} }
async function configuredFrames() {
  const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-frames-'))
  const config: BridgeConfig = { root, python, data: [], allowRequest: true, tools: [], execution: configuredProfile }
  const ctx = await createApplication(config)
  const id = SessionId('configured-frames')
  const route = selectedModel(config)
  const invoke = ctx.rpnhCapabilities.forAgent({ id } as any)
  const frame = (kind: 'model_request' | 'model_response', version: string, arguments_: Record<string, unknown>) => ({
    ticket: { kind, session_id: id, execution_ref: { version_id: version } }, arguments: arguments_,
  })
  const argumentsFor = (messages: unknown[], tools: unknown = []) => ({ route, messages, registered_tools: tools,
    step: 0, request: { ...route, messages, sessionId: id, maxTokens: 64 } })
  return { config, ctx, route, invoke, frame, argumentsFor,
    cleanup: async () => { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) } }
}

describe('Registry-backed DSH integration', () => {
  it('keeps an empty configured tool catalog on the existing text-only provider request', async () => {
    const f = await configuredFrames()
    try {
      const messages = [{ role: 'user', content: [{ type: 'text', text: 'reply once' }] }]
      const result = await f.invoke(f.frame('model_request', 'empty-tools', f.argumentsFor(messages)), new AbortController().signal)
      expect(result).toEqual({ provider_request: { protocol: 'registered_llm/v1',
        messages: [{ role: 'user', content: 'reply once' }], tools: [], tool_choice: 'none', placeholders: [] } })
    } finally { await f.cleanup() }
  })

  it('projects one correlated managed tool round into exact registered provider DTO messages', async () => {
    const f = await configuredFrames()
    try {
      const messages = [
        { role: 'user', content: [{ type: 'text', text: 'sum these' }] },
        { role: 'assistant', content: [{ type: 'reasoning', text: 'I should delegate. ' }, { type: 'text', text: 'Calling it.' },
          { type: 'tool-call', id: 'call-1', name: 'managed_sum', arguments: '{"values":[2,3]}' }] },
        { role: 'user', source: { kind: 'tool', callId: 'call-1' }, content: [{ type: 'tool-result', toolCallId: 'call-1', isError: false,
          content: [{ type: 'text', text: '5' }] }] },
      ]
      const result = await f.invoke(f.frame('model_request', 'managed-round', f.argumentsFor(messages, registeredTools)), new AbortController().signal)
      expect(result).toEqual({ provider_request: { protocol: 'registered_llm/v1', tools: registeredTools, tool_choice: 'auto', placeholders: [], messages: [
        { role: 'user', content: 'sum these' },
        { role: 'assistant', content: 'I should delegate. Calling it.', tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'managed_sum', arguments: '{"values":[2,3]}' } }] },
        { role: 'tool', tool_call_id: 'call-1', content: '5' },
      ] } })
      const failedMessages = [
        { role: 'assistant', content: [{ type: 'tool-call', id: 'call-2', name: 'managed_sum', arguments: '{"values":[2,3]}' }] },
        { role: 'user', source: { kind: 'tool', callId: 'call-2' }, content: [{ type: 'tool-result', toolCallId: 'call-2', isError: true,
          content: [{ type: 'text', text: '{"error":"handler_failed"}' }] }] },
      ]
      const failed = await f.invoke(f.frame('model_request', 'managed-error-round', f.argumentsFor(failedMessages, registeredTools)), new AbortController().signal)
      expect(failed).toEqual({ provider_request: { protocol: 'registered_llm/v1', tools: registeredTools, tool_choice: 'auto', placeholders: [], messages: [
        { role: 'assistant', tool_calls: [{ id: 'call-2', type: 'function', function: { name: 'managed_sum', arguments: '{"values":[2,3]}' } }] },
        { role: 'tool', tool_call_id: 'call-2', content: '{"error":"handler_failed"}' },
      ] } })
    } finally { await f.cleanup() }
  })

  it('rejects configured provider tool calls outside the registered catalog', async () => {
    const f = await configuredFrames()
    try {
      const args = f.argumentsFor([{ role: 'user', content: [{ type: 'text', text: 'go' }] }], registeredTools)
      await f.invoke(f.frame('model_request', 'undeclared-response', args), new AbortController().signal)
      await expect(f.invoke(f.frame('model_response', 'undeclared-response', { ...args, provider_response: {
        protocol: 'llm_response_envelope/v1', text: '', tool_calls: [{ id: 'call-1', name: 'not_registered', arguments: '{}' }], finish_reason: 'tool_calls',
      } }), new AbortController().signal)).rejects.toThrow('invalid tool call')
    } finally { await f.cleanup() }
  })

  it('rejects malformed or unmatched configured tool results', async () => {
    const f = await configuredFrames()
    try {
      const base = [{ role: 'assistant', content: [{ type: 'tool-call', id: 'call-1', name: 'managed_sum', arguments: '{}' }] }]
      await expect(f.invoke(f.frame('model_request', 'unmatched-result', f.argumentsFor([...base,
        { role: 'user', source: { kind: 'tool', callId: 'other' }, content: [{ type: 'tool-result', toolCallId: 'other', isError: false, content: [{ type: 'text', text: '5' }] }] },
      ], registeredTools)), new AbortController().signal)).rejects.toThrow('malformed or uncorrelated')
      await expect(f.invoke(f.frame('model_request', 'malformed-result', f.argumentsFor([...base,
        { role: 'user', source: { kind: 'tool', callId: 'call-1' }, content: [{ type: 'tool-result', toolCallId: 'call-1', isError: false, content: [{ type: 'reasoning', text: '5' }] }] },
      ], registeredTools)), new AbortController().signal)).rejects.toThrow('malformed or uncorrelated')
    } finally { await f.cleanup() }
  })

  it('rejects multiple configured assistant tool calls', async () => {
    const f = await configuredFrames()
    try {
      await expect(f.invoke(f.frame('model_request', 'parallel-calls', f.argumentsFor([{ role: 'assistant', content: [
        { type: 'tool-call', id: 'call-1', name: 'managed_sum', arguments: '{}' },
        { type: 'tool-call', id: 'call-2', name: 'managed_sum', arguments: '{}' },
      ] }], registeredTools)), new AbortController().signal)).rejects.toThrow('parallel tool calls')
    } finally { await f.cleanup() }
  })

  it('projects one configured launcher profile and rejects an agent route override without a provider call', async () => {
    const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-profile-'))
    const adapterPath = join(root, 'adapter.json')
    const selectionPath = join(root, 'selection.json')
    await writeFile(adapterPath, JSON.stringify({
      schema_version: 'local_process_adapter_config/v1', adapter_kind: 'local_process',
      model_condition: 'test-model', argv: [python, '-c', 'raise SystemExit(1)'],
      probe_argv: [python, '-c', 'raise SystemExit(1)'], env: {}, inherit_env: [],
    }))
    await writeFile(selectionPath, JSON.stringify({
      schema_version: 'llm_execution_selection/v1', adapter_kind: 'local_process',
      model_condition: 'test-model', adapter_config_path: adapterPath,
      timeout_seconds: 30, max_output_tokens: 64, max_response_bytes: 65536,
    }))
    const config: BridgeConfig = { root, python, data: [], allowRequest: true, tools: [], execution: {
      kind: 'configured', selectionPath, profile: {
        schema_version: 'rpnh/dsh_execution_profile/v2', profile: 'test-profile',
        selection_id: 'test-provider/test-model', provider: 'test-provider',
        provider_display_name: 'Test provider', model_condition: 'test-model',
        adapter_kind: 'local_process', transport_kind: 'subprocess',
        timeout_seconds: 30, max_output_tokens: 64, max_response_bytes: 65536,
      } } }
    const projection = selectedModel(config)
    expect(projection).toEqual({ provider: 'test-provider', model: 'test-model' })
    expect(Object.isFrozen(projection)).toBe(true)
    const ctx = await createApplication(config)
    try {
      await expect(ctx.agents.create({ sessionId: SessionId('wrong-route'), agentOptions: options }))
        .rejects.toThrow('no implicit model fallback or per-turn route override')
      expect(ctx.rpnhCapabilities.counters).toMatchObject({ model: 0, tools: 0 })
    } finally { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) }
  })

  it('runs configured text through the shared local-process provider boundary', async () => {
    const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-configured-'))
    const providerPath = join(root, 'provider.py')
    const adapterPath = join(root, 'adapter.json')
    const selectionPath = join(root, 'selection.json')
    await writeFile(providerPath, [
      'import json, sys',
      'request = json.load(sys.stdin)',
      "assert request['protocol'] == 'llm_request_envelope/v1'",
      "assert request['model_condition'] == 'configured-model'",
      "assert request['messages'][-1] == {'role': 'user', 'content': 'reply once'}",
      "response = {'protocol':'llm_response_envelope/v1','text':'configured ready','tool_calls':[],'finish_reason':'stop'}",
      "sys.stdout.write(json.dumps(response, sort_keys=True, separators=(',', ':')))",
    ].join('\n'))
    await writeFile(adapterPath, JSON.stringify({
      schema_version: 'local_process_adapter_config/v1', adapter_kind: 'local_process',
      model_condition: 'configured-model', argv: [python, providerPath],
      probe_argv: [python, '-c', 'raise SystemExit(0)'], env: {}, inherit_env: [],
    }))
    await writeFile(selectionPath, JSON.stringify({
      schema_version: 'llm_execution_selection/v1', adapter_kind: 'local_process',
      model_condition: 'configured-model', adapter_config_path: adapterPath,
      timeout_seconds: 30, max_output_tokens: 64, max_response_bytes: 65536,
    }))
    const config: BridgeConfig = { root: join(root, 'sessions'), python, data: [], allowRequest: true, tools: [], execution: {
      kind: 'configured', selectionPath, profile: {
        schema_version: 'rpnh/dsh_execution_profile/v2', profile: 'local-test',
        selection_id: 'local-test/configured-model', provider: 'local-test',
        provider_display_name: 'Local test', model_condition: 'configured-model',
        adapter_kind: 'local_process', transport_kind: 'subprocess',
        timeout_seconds: 30, max_output_tokens: 64, max_response_bytes: 65536,
      } } }
    const ctx = await createApplication(config)
    try {
      const selected = selectedModel(config)
      const h = await ctx.agents.create({ sessionId: SessionId('configured-text'), agentOptions: selected })
      h.agent.followup(message('reply once'))
      await h.agent.whenIdle()
      const a = h.agent as RegistryAgent
      expect(a.lastOutcome?.answer?.text, a.lastError).toBe('configured ready')
      expect(ctx.rpnhCapabilities.counters).toMatchObject({ model: 1, tools: 0 })
      expect(new Set(ctx.rpnhCapabilities.counters.tickets).size).toBe(1)
      await h.dispose()
    } finally { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) }
  }, 120_000)

  it('runs actual DSH model/tools through PN, and reopens only registered history', async () => {
    const f = await fixture()
    try {
      const h = await agent(f.ctx, 'session-one')
      h.agent.followup(message('read the data and compute the sum'))
      await h.agent.whenIdle()
      const a = h.agent as RegistryAgent
      expect(a.lastOutcome?.answer?.text, JSON.stringify(a.session.snapshotEvents())).toBe('Sum: 12')
      expect(f.ctx.rpnhCapabilities.counters).toMatchObject({ model: 3, tools: 2 })
      expect(new Set(f.ctx.rpnhCapabilities.counters.tickets).size).toBe(5)
      await f.ctx.sessions.flush(a.session)
      const before = a.session.deriveMessages()
      await h.dispose()
      const reopened = await f.ctx.agents.resume({ resumeSessionId: SessionId('session-one'), agentOptions: options })
      expect(reopened.agent.session.deriveMessages()).toEqual(before)
      expect(f.ctx.rpnhCapabilities.counters.model).toBe(3)
      await f.ctx.sessions.flush(reopened.agent.session)
      reopened.agent.session.append('user/message', message('forged formal context'), { surfaceOp: 'append' })
      await expect(f.ctx.sessions.flush(reopened.agent.session)).rejects.toThrow('DSH surface differs from Registry')
      await reopened.dispose()
    } finally { await f.cleanup() }
  }, 300_000)

  it('deny produces zero model and tool side effects', async () => {
    const f = await fixture(false)
    try {
      const h = await agent(f.ctx, 'denied')
      h.agent.followup(message('must not run'))
      await h.agent.whenIdle()
      expect((h.agent as RegistryAgent).lastOutcome?.answer?.status, JSON.stringify(h.agent.session.snapshotEvents())).toBe('denied')
      expect(f.ctx.rpnhCapabilities.counters).toMatchObject({ model: 0, tools: 0 })
      expect(h.agent.session.snapshotEvents().filter(e => e.type === 'assistant/message')).toEqual([])
      await h.dispose()
    } finally { await f.cleanup() }
  }, 120_000)

  it('tool denial and direct/out-of-band calls cannot bypass the PN grant', async () => {
    const f = await fixture(true, [])
    try {
      const h = await agent(f.ctx, 'tool-denied')
      const result = await f.ctx.tools.execute({ callId: ToolCallId('forged'), name: 'read_dataset', arguments: {}, agent: h.agent, signal: new AbortController().signal })
      expect(result.isError).toBe(true)
      h.agent.followup(message('try reading'))
      await h.agent.whenIdle()
      expect((h.agent as RegistryAgent).lastOutcome?.answer?.status, JSON.stringify(h.agent.session.snapshotEvents())).toBe('denied')
      expect(f.ctx.rpnhCapabilities.counters).toMatchObject({ model: 1, tools: 0 })
      await h.dispose()
    } finally { await f.cleanup() }
  }, 120_000)

  it('factory setup rollback publishes neither a half-configured agent nor fallback driver', async () => {
    const f = await fixture()
    try {
      await expect(f.ctx.agents.create({ sessionId: SessionId('rollback'), agentOptions: options,
        setup: async () => { throw new Error('setup rejected') } })).rejects.toThrow('setup rejected')
      expect(f.ctx.agents.get(SessionId('rollback'))).toBeUndefined()
      expect(f.ctx.sessions.get(SessionId('rollback'))).toBeUndefined()
      expect(f.ctx.rpnhCapabilities.counters.model).toBe(0)
    } finally { await f.cleanup() }
  }, 60_000)

  it('maintenance excludes execution and idle inject does not wake the model', async () => {
    const f = await fixture()
    try {
      const h = await agent(f.ctx, 'maintenance')
      h.agent.inject(message('context'))
      await h.agent.whenIdle()
      expect(f.ctx.rpnhCapabilities.counters.model).toBe(0)
      let release!: () => void
      const maintenance = h.agent.runMaintenance(() => new Promise<void>(r => { release = r }))
      await Promise.resolve()
      h.agent.followup(message('sum'))
      expect(h.agent.status).toBe('idle')
      expect(f.ctx.rpnhCapabilities.counters.model).toBe(0)
      release(); await maintenance; await h.agent.whenIdle()
      expect((h.agent as RegistryAgent).lastOutcome?.answer?.text, JSON.stringify(h.agent.session.snapshotEvents())).toBe('Sum: 12')
      await h.dispose()
    } finally { await f.cleanup() }
  }, 300_000)

  it('runs the real upstream headless application in a separate Node process', async () => {
    const root = await mkdtemp(join(tmpdir(), 'rpnh-dsh-app-'))
    try {
      const data = join(root, 'numbers.json'); await writeFile(data, '[2,3,7]')
      const result = await new Promise<{ code: number | null; out: string; err: string }>((resolveDone, reject) => {
        const child = spawn('bash', [resolve(process.env.RPNH_DSH_SOURCE!, 'integrations/dsh/run.sh'), python, process.cwd(),
          '--offline', '--root', 'sessions', '--data-file', 'numbers.json', '--task', 'read and sum'],
          { cwd: root, env: { ...process.env, TSX_TSCONFIG_PATH: resolve('tsconfig.base.json') } })
        let out = ''; let err = ''
        child.stdout.on('data', b => { out += b }); child.stderr.on('data', b => { err += b })
        child.on('error', reject); child.on('exit', code => resolveDone({code,out,err}))
      })
      expect(result, result.err).toMatchObject({code:0})
      expect(result.out).toContain('Sum: 12')
      // Socket infrastructure is not a session. Bind to the explicit ID
      // reported by this successful application, then verify its directory.
      const ids = [...result.err.matchAll(/^RPNH session: (session-[a-zA-Z0-9-]+)$/gm)].map(m => m[1])
      expect(ids).toHaveLength(1)
      const sessionId = ids[0]
      expect(await readdir(join(root, 'sessions'))).toContain(sessionId)
      const read = await new Promise<{ code: number | null; out: string; err: string }>((resolveDone, reject) => {
        const child = spawn('bash', [resolve(process.env.RPNH_DSH_SOURCE!, 'integrations/dsh/run.sh'), python, process.cwd(),
          '--history', '--root', 'sessions', '--session-id', sessionId],
          { cwd: root, env: { ...process.env, TSX_TSCONFIG_PATH: resolve('tsconfig.base.json') } })
        let out = ''; let err = ''
        child.stdout.on('data', b => { out += b }); child.stderr.on('data', b => { err += b })
        child.on('error', reject); child.on('exit', code => resolveDone({ code, out, err }))
      })
      expect(read, read.err).toMatchObject({ code: 0 })
      const history = JSON.parse(read.out)
      expect(history.committed_history[0].answer.text).toBe('Sum: 12')
      expect(history.active).toBeNull()

    } finally { await rm(root, {recursive:true,force:true}) }
  }, 300_000)
})
