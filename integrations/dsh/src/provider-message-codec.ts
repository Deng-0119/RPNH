/** Pure DTO projection. Callers supply the exact revision; this grants no execution. */
import { decodeToolResult, dialectForRevision, type JsonRecord } from './message-codec.ts'
const record = (value: unknown): JsonRecord | undefined => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : undefined
function exactToolArguments(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try { const parsed = JSON.parse(value); return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) }
  catch { return false }
}
export interface ProviderToolCall { id: string; name: string; arguments: string }
/** Validate one provider batch at its DTO boundary without rewriting arguments. */
export function providerToolCalls(value: unknown, toolNames: Set<string>): ProviderToolCall[] {
  if (!Array.isArray(value)) throw new Error('configured provider tool calls require an array')
  const ids = new Set<string>()
  return value.map(raw => {
    const call = record(raw)
    if (!call || typeof call.id !== 'string' || !call.id || typeof call.name !== 'string' || !toolNames.has(call.name)
      || !exactToolArguments(call.arguments) || ('raw_arguments' in call && call.raw_arguments !== call.arguments)
      || ids.has(call.id)) {
      throw new Error('configured provider contains an invalid tool call or duplicate call identity')
    }
    ids.add(call.id)
    return { id: call.id, name: call.name, arguments: call.arguments }
  })
}
export function providerMessages(messages: unknown, toolNames: Set<string>, revision: unknown): JsonRecord[] {
  dialectForRevision(revision)
  if (!Array.isArray(messages)) throw new Error('configured DSH provider requests require a message array')
  const output: JsonRecord[] = []
  // Insertion order is the model's call order, independent of completion order.
  const pending = new Map<string, ProviderToolCall>()
  const results = new Map<string, JsonRecord>()
  for (const rawMessage of messages) {
    const message = record(rawMessage)
    if (!message || !Array.isArray(message.content) || (message.content.length === 0 && message.role !== 'tool')) {
      throw new Error('configured DSH provider requests contain an invalid message')
    }
    const blocks = message.content.map(record)
    if (blocks.some(block => !block)) throw new Error('configured DSH provider requests contain an invalid block')
    if (message.role === 'assistant') {
      if (pending.size) throw new Error('configured DSH tool call has no correlated result')
      const calls = blocks.filter(block => block!.type === 'tool-call')
      if (blocks.some(block => !['text', 'reasoning', 'tool-call'].includes(block!.type))) {
        throw new Error('configured DSH provider requests contain an unknown assistant block')
      }
      const content = blocks.filter(block => block!.type !== 'tool-call').map(block => {
        if (typeof block!.text !== 'string') throw new Error('configured DSH provider requests contain invalid assistant text')
        return block!.text
      }).join('')
      if (!calls.length) {
        output.push({ role: 'assistant', content })
        continue
      }
      for (const call of providerToolCalls(calls, toolNames)) pending.set(call.id, call)
      output.push({ role: 'assistant', ...(content ? { content } : {}), tool_calls: [...pending.values()].map(call => ({
        id: call.id, type: 'function', function: { name: call.name, arguments: call.arguments },
      })) })
      continue
    }
    const source = record(message.source)
    const toolResult = source?.kind === 'tool' || message.role === 'tool'
    if (!toolResult) {
      if (message.role !== 'user') throw new Error('configured DSH provider requests contain an unsupported role')
      if (pending.size || blocks.some(block => block!.type !== 'text' || typeof block!.text !== 'string')) {
        throw new Error('configured DSH provider requests contain an invalid user message')
      }
      output.push({ role: 'user', content: blocks.map(block => block!.text).join('') })
      continue
    }
    let result; let content: string
    try {
      result = decodeToolResult(message, revision, { requireError: true })
      if (result.content.some(block => block.type !== 'text' || typeof block.text !== 'string')) {
        throw new Error('configured tool result requires text blocks')
      }
      content = result.content.map(block => block.text).join('')
    } catch { throw new Error('configured DSH tool result is malformed or uncorrelated') }
    if (!pending.has(result.callId) || results.has(result.callId)) {
      throw new Error('configured DSH tool result is malformed or uncorrelated')
    }
    results.set(result.callId, { role: 'tool', tool_call_id: result.callId, content })
    if (results.size === pending.size) {
      for (const id of pending.keys()) output.push(results.get(id)!)
      pending.clear(); results.clear()
    }
  }
  if (pending.size) throw new Error('configured DSH tool call has no correlated result')
  return output
}
