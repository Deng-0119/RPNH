/** Pure DTO projection. Callers supply the exact revision; this grants no execution. */
import { decodeToolResult, toolResultText, dialectForRevision, type JsonRecord } from './message-codec.ts'
const record = (value: unknown): JsonRecord | undefined => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : undefined
function exactToolArguments(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try { const parsed = JSON.parse(value); return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) }
  catch { return false }
}
export function providerMessages(messages: unknown, toolNames: Set<string>, revision: unknown): JsonRecord[] {
  dialectForRevision(revision)
  if (!Array.isArray(messages)) throw new Error('configured DSH provider requests require a message array')
  const output: JsonRecord[] = []
  let pending: { id: string; name: string; arguments: string } | undefined
  for (const rawMessage of messages) {
    const message = record(rawMessage)
    if (!message || !Array.isArray(message.content) || (message.content.length === 0 && message.role !== 'tool')) {
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
    const source = record(message.source)
    const toolResult = source?.kind === 'tool' || message.role === 'tool'
    if (!toolResult) {
      if (message.role !== 'user') throw new Error('configured DSH provider requests contain an unsupported role')
      if (pending || blocks.some(block => block!.type !== 'text' || typeof block!.text !== 'string')) {
        throw new Error('configured DSH provider requests contain an invalid user message')
      }
      output.push({ role: 'user', content: blocks.map(block => block!.text).join('') })
      continue
    }
    let result; let content: string
    try {
      result = decodeToolResult(message, revision, { requireError: true })
      content = toolResultText(message, revision)
    } catch { throw new Error('configured DSH tool result is malformed or uncorrelated') }
    if (!pending || result.callId !== pending.id) {
      throw new Error('configured DSH tool result is malformed or uncorrelated')
    }
    output.push({ role: 'tool', tool_call_id: pending.id, content })
    pending = undefined
  }
  if (pending) throw new Error('configured DSH tool call has no correlated result')
  return output
}
