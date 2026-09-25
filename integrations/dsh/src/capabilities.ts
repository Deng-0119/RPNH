import { isDeepStrictEqual } from 'node:util'
import { AsyncLocalStorage } from 'node:async_hooks'
import { Context, Service } from '@deepseek-ai/cordis'
import { LlmAdapter, BlockAssembler, ToolCallId, createAssistantMessage, createToolResultMessage } from '@deepseek-ai/dsh-llm'
import type { GenerateOptions, StreamChunk } from '@deepseek-ai/dsh-llm'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { JsonRecord, EffectHost } from './bridge.ts'
type Grant = { frame: JsonRecord; used: boolean; raw?: unknown }
const equal = (a: unknown, b: unknown): boolean => isDeepStrictEqual(a, b)
const record = (value: unknown): JsonRecord | undefined => value !== null && typeof value === 'object' && !Array.isArray(value)
  ? value as JsonRecord : undefined
function registeredToolNames(tools: unknown): Set<string> {
  if (!Array.isArray(tools)) throw new Error('configured registered tools must be an array')
  const names = new Set<string>()
  for (const tool of tools) {
    const declaration = record(tool); const fn = record(declaration?.function)
    const parameters = record(fn?.parameters)
    if (!declaration || Object.keys(declaration).sort().join(',') !== 'function,type'
      || declaration.type !== 'function' || !fn
      || Object.keys(fn).sort().join(',') !== 'description,name,parameters'
      || typeof fn.name !== 'string' || !fn.name || typeof fn.description !== 'string'
      || !parameters || parameters.type !== 'object'
      || names.has(fn.name)) throw new Error('configured registered tools must contain unique function declarations')
    names.add(fn.name)
  }
  return names
}
function exactToolArguments(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try {
    const parsed = JSON.parse(value)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
  } catch { return false }
}
function providerMessages(messages: unknown, toolNames: Set<string>): JsonRecord[] {
  if (!Array.isArray(messages)) throw new Error('configured DSH provider requests require a message array')
  const output: JsonRecord[] = []
  let pending: { id: string; name: string; arguments: string } | undefined
  for (const rawMessage of messages) {
    const message = record(rawMessage)
    if (!message || !Array.isArray(message.content) || message.content.length === 0) {
      throw new Error('configured DSH provider requests contain an invalid message')
    }
    const blocks = message.content.map(record)
    if (blocks.some(block => !block)) throw new Error('configured DSH provider requests contain an invalid block')
    if (message.role === 'assistant') {
      if (pending) throw new Error('configured DSH tool call has no correlated result')
      const calls = blocks.filter(block => block!.type === 'tool-call')
      if (calls.length > 1) throw new Error('configured DSH provider requests do not support parallel tool calls')
      if (blocks.some(block => !['text', 'reasoning', 'tool-call'].includes(block!.type))) {
        throw new Error('configured DSH provider requests contain an unknown assistant block')
      }
      const content = blocks.filter(block => block!.type !== 'tool-call').map(block => {
        if (typeof block!.text !== 'string') throw new Error('configured DSH provider requests contain invalid assistant text')
        return block!.text
      }).join('')
      const call = calls[0]
      if (!call) {
        output.push({ role: 'assistant', content })
        continue
      }
      if (typeof call.id !== 'string' || !call.id || typeof call.name !== 'string' || !toolNames.has(call.name)
        || !exactToolArguments(call.arguments) || ('raw_arguments' in call && call.raw_arguments !== call.arguments)) {
        throw new Error('configured DSH provider requests contain an invalid or changed tool call')
      }
      pending = { id: call.id, name: call.name, arguments: call.arguments }
      output.push({ role: 'assistant', ...(content ? { content } : {}), tool_calls: [{ id: call.id, type: 'function',
        function: { name: call.name, arguments: call.arguments } }] })
      continue
    }
    if (message.role !== 'user') throw new Error('configured DSH provider requests contain an unsupported role')
    const source = record(message.source)
    if (source?.kind !== 'tool') {
      if (pending || blocks.some(block => block!.type !== 'text' || typeof block!.text !== 'string')) {
        throw new Error('configured DSH provider requests contain an invalid user message')
      }
      output.push({ role: 'user', content: blocks.map(block => block!.text).join('') })
      continue
    }
    const result = blocks.length === 1 ? blocks[0] : undefined
    const resultContent = result && Array.isArray(result.content) ? result.content.map(record) : undefined
    if (!pending || source.callId !== pending.id || !result || result.type !== 'tool-result'
      || result.toolCallId !== pending.id || typeof result.isError !== 'boolean' || !resultContent || resultContent.some(block => !block || block.type !== 'text' || typeof block.text !== 'string')) {
      throw new Error('configured DSH tool result is malformed or uncorrelated')
    }
    output.push({ role: 'tool', tool_call_id: pending.id, content: resultContent.map(block => block!.text).join('') })
    pending = undefined
  }
  if (pending) throw new Error('configured DSH tool call has no correlated result')
  return output
}
export interface PhysicalCounters { model: number; tools: number; tickets: string[] }
declare module '@deepseek-ai/cordis' { interface Context { rpnhCapabilities: CapabilityHost } }
export class CapabilityHost extends Service {
  static inject = ['llm', 'tools', 'rpnhBridge']
  readonly counters: PhysicalCounters = { model: 0, tools: 0, tickets: [] }
  private grants = new AsyncLocalStorage<Grant>()
  private consumed = new Set<string>()
  private prepared = new Map<string, { ticket: JsonRecord; arguments: JsonRecord; providerRequest: JsonRecord }>()
  private context: Context
  constructor(ctx: Context) {
    super(ctx, 'rpnhCapabilities')
    this.context = ctx
    if (ctx.rpnhBridge.config.execution.kind === 'offline') {
      ctx.llm.registerAdapter(['rpnh-offline'], new OfflineAdapter(this))
    }
    for (const name of ['read_dataset', 'sum_values']) {
      ctx.tools.register({ name, description: name === 'read_dataset' ? 'Read the explicitly registered numeric data snapshot.' : 'Sum values from the exact admitted read result.',
        parameters: name === 'read_dataset' ? { type: 'object', additionalProperties: false, properties: {} }
          : { type: 'object', additionalProperties: false, properties: { values: { type: 'array', items: { type: 'number' } } }, required: ['values'] },
        output: { schema: name === 'read_dataset' ? { type: 'array', items: { type: 'number' } } : { type: 'number' },
          render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (args, exec) => {
          const g = this.require('tool')
          if (g.used || g.frame.arguments.call.name !== name || !equal(g.frame.arguments.call.arguments, args)) throw new Error('exact tool grant mismatch')
          exec.signal.throwIfAborted()
          g.used = true; this.counters.tools++
          this.counters.tickets.push(g.frame.ticket.execution_ref.version_id)
          const value = name === 'read_dataset' ? structuredClone(g.frame.arguments.data)
            : (args as {values:number[]}).values.reduce((a,b) => a+b, 0)
          g.raw = structuredClone(value)
          return value
        } })
    }
    ctx.tools.guard(exec => {
      const g = this.grants.getStore()
      return g !== undefined && !g.used && g.frame.ticket.kind === 'tool' && exec.parent === undefined
        && exec.name === g.frame.arguments.call.name && equal(exec.arguments, g.frame.arguments.call.arguments)
        ? undefined : 'No exact Registry/PN execution grant; nested dispatch is disabled.'
    })
  }
  require(kind: string): Grant {
    const g = this.grants.getStore()
    if (!g || g.frame.ticket.kind !== kind) throw new Error('No Registry/PN execution grant')
    return g
  }
  modelDispatched(options: GenerateOptions): Grant {
    const g = this.require('model'); const args = g.frame.arguments
    if (g.used || options.provider !== args.route.provider || options.model !== args.route.model
      || options.sessionId !== g.frame.ticket.session_id || !equal(options.messages, args.messages)) throw new Error('exact model request differs or was already dispatched')
    const { signal: _signal, ...wire } = options
    if (!equal(wire, args.request)) throw new Error('unregistered model request transformation')
    g.used = true; this.counters.model++; this.counters.tickets.push(g.frame.ticket.execution_ref.version_id)
    return g
  }
  private configuredRequest(agent: Agent, frame: JsonRecord): JsonRecord {
    if (this.context.rpnhBridge.config.execution.kind !== 'configured') throw new Error('configured request used outside configured execution')
    const args = frame.arguments
    const route = this.context.rpnhBridge.model
    if (frame.ticket.session_id !== agent.id || !equal(args.route, route)
      || !Array.isArray(args.messages) || !equal(args.request, { ...route, messages: args.messages,
        sessionId: agent.id, maxTokens: this.context.rpnhBridge.config.execution.profile.max_output_tokens })) {
      throw new Error('configured DSH request differs from the launcher-managed route')
    }
    const registeredTools = args.registered_tools
    const toolNames = registeredToolNames(registeredTools)
    const messages = providerMessages(args.messages, toolNames)
    const providerRequest = { protocol: 'registered_llm/v1', messages, tools: registeredTools,
      tool_choice: registeredTools.length === 0 ? 'none' : 'auto', placeholders: [] }
    this.prepared.set(frame.ticket.execution_ref.version_id, {
      ticket: structuredClone(frame.ticket), arguments: structuredClone(args), providerRequest,
    })
    return { provider_request: providerRequest }
  }
  private configuredResponse(agent: Agent, frame: JsonRecord): JsonRecord {
    if (this.context.rpnhBridge.config.execution.kind !== 'configured') throw new Error('configured response used outside configured execution')
    const key = frame.ticket.execution_ref.version_id
    const prepared = this.prepared.get(key)
    if (!prepared) throw new Error('configured response has no exact prepared request')
    const { provider_response: response, ...requestArguments } = frame.arguments
    const { kind: _preparedKind, ...preparedTicket } = prepared.ticket
    const { kind: _responseKind, ...responseTicket } = frame.ticket
    if (frame.ticket.session_id !== agent.id || !equal(preparedTicket, responseTicket)
      || !equal(requestArguments, prepared.arguments) || !response || typeof response !== 'object'
      || response.protocol !== 'llm_response_envelope/v1' || !Array.isArray(response.tool_calls)) {
      throw new Error('configured response differs from its exact provider request')
    }
    const content: JsonRecord[] = []
    const chunks: StreamChunk[] = []
    const toolNames = registeredToolNames(prepared.arguments.registered_tools)
    let index = 0
    if (typeof response.reasoning_content === 'string') {
      const block = { type: 'reasoning' as const, text: response.reasoning_content }
      content.push(block); chunks.push({ type: 'block-start', index, blockType: 'reasoning' }, { type: 'block-end', index, block }); index++
    }
    if (typeof response.text === 'string') {
      const block = { type: 'text' as const, text: response.text }
      content.push(block); chunks.push({ type: 'block-start', index, blockType: 'text' }, { type: 'block-end', index, block }); index++
    }
    if (response.tool_calls.length > 1) throw new Error('configured provider response contains parallel tool calls')
    for (const raw of response.tool_calls) {
      if (!raw || typeof raw !== 'object' || typeof raw.id !== 'string' || !raw.id
        || typeof raw.name !== 'string' || !toolNames.has(raw.name) || !exactToolArguments(raw.arguments)) {
        throw new Error('configured provider response contains an invalid tool call')
      }
      const block = { type: 'tool-call' as const, id: ToolCallId(raw.id), name: raw.name, arguments: raw.arguments }
      content.push(block); chunks.push({ type: 'block-start', index, blockType: 'tool-call' }, { type: 'block-end', index, block }); index++
    }
    if (content.length === 0) throw new Error('configured provider response contains no DSH message content')
    const finish = response.finish_reason === 'length' ? { kind: 'max-tokens' as const }
      : response.tool_calls.length > 0 && (response.finish_reason === 'tool_calls' || response.finish_reason === null || response.finish_reason === undefined)
        ? { kind: 'tool-calls' as const }
        : response.tool_calls.length === 0 && (response.finish_reason === 'stop' || response.finish_reason === null || response.finish_reason === undefined)
          ? { kind: 'stop' as const }
          : undefined
    if (!finish) throw new Error('configured provider response has an unsupported finish reason')
    chunks.push({ type: 'finish', reason: finish })
    const message = createAssistantMessage({ source: frame.arguments.route, content: content as any })
    this.prepared.delete(key)
    this.counters.model++; this.counters.tickets.push(key)
    return { message, chunks, finish, wire_request: prepared.providerRequest }
  }
  forAgent(agent: Agent): EffectHost {
    return async (frame, signal) => {
      const executionKey = frame.ticket.execution_ref.version_id
      const key = `${executionKey}:${frame.ticket.kind}`
      if (this.consumed.has(key) || frame.ticket.session_id !== agent.id) throw new Error('reused or foreign execution ticket')
      this.consumed.add(key)
      if (frame.ticket.kind === 'model_request') {
        signal.throwIfAborted()
        return this.configuredRequest(agent, frame)
      }
      if (frame.ticket.kind === 'model_response') {
        signal.throwIfAborted()
        return this.configuredResponse(agent, frame)
      }
      if (this.context.rpnhBridge.config.execution.kind !== 'offline') {
        throw new Error('configured execution accepts only registered provider DTO phases')
      }
      return await this.grants.run({ frame, used: false }, async () => {
        if (frame.ticket.kind === 'model') {
          const prepared = await agent.ctx.llm.prepareCall(frame.arguments.route, signal)
          const options: GenerateOptions = { ...frame.arguments.request, signal }
          const assembly = new BlockAssembler(); const chunks: StreamChunk[] = []
          for await (const chunk of prepared.stream(options)) { chunks.push(chunk); assembly.push(chunk) }
          if (!this.require('model').used) throw new Error('model pipeline returned without a physical dispatch')
          return { message: assembly.message({ kind: 'model', ...frame.arguments.route }), chunks,
                   finish: assembly.finish, wire_request: { ...options, signal: undefined } }
        }
        if (frame.ticket.kind !== 'tool') throw new Error('unknown capability kind')
        const call = frame.arguments.call
        const result = await agent.ctx.tools.execute({ callId: ToolCallId(call.id), name: call.name,
          arguments: call.arguments, agent, signal })
        const g = this.require('tool')
        if (!g.used || result.additionalContexts?.length || result.concludesTurn) throw new Error('unsupported tool interception/context operation')
        return { ...result, raw_value: g.raw,
          message: createToolResultMessage({ callId: ToolCallId(call.id), content: result.content, isError: result.isError }) }
      })
    }
  }
}

/** Offline external-provider substitute; LlmRuntime itself is genuine DSH. */
class OfflineAdapter extends LlmAdapter {
  constructor(private readonly host: CapabilityHost) { super() }
  async *stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    const g = this.host.modelDispatched(options)
    options.signal?.throwIfAborted()
    const step = g.frame.arguments.step
    let block: any
    if (step === 0) block = { type: 'tool-call', id: ToolCallId(`read-${g.frame.ticket.request_id}`), name: 'read_dataset', arguments: '{}' }
    else if (step === 1) {
      const last: any = options.messages.at(-1)
      const value = JSON.parse(last.content[0].content[0].text)
      block = { type: 'tool-call', id: ToolCallId(`sum-${g.frame.ticket.request_id}`), name: 'sum_values', arguments: JSON.stringify({ values: value }) }
    } else {
      const last: any = options.messages.at(-1)
      block = { type: 'text', text: `Sum: ${last.content[0].content[0].text}` }
    }
    yield { type: 'block-start', index: 0, blockType: block.type }
    yield { type: 'block-end', index: 0, block }
    yield { type: 'finish', reason: { kind: block.type === 'tool-call' ? 'tool-calls' : 'stop' } }
  }
}
