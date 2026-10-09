/** Exact-revision message layout codec. Recognition does not authorize execution. */
import { isAbsolute } from 'node:path'
export const LEGACY_DSH_REVISION = 'ddefc45fbc7f8e46dd73185e68295696d1297887'
export const CANDIDATE_DSH_REVISION = '5badb15009ae1756c3afe0ae0cef1faafc290ccc'
export type DshDialect = 3 | 4
export type JsonRecord = Record<string, any>
export class DshCodecError extends Error {}
const fail = (message: string): never => { throw new DshCodecError(message) }
export const record = (value: unknown): value is JsonRecord =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const nonempty = (value: unknown): value is string => typeof value === 'string' && value.length > 0
export function dialectForRevision(revision: unknown): DshDialect {
  if (revision === LEGACY_DSH_REVISION) return 3
  if (revision === CANDIDATE_DSH_REVISION) return 4
  return fail('unsupported exact DSH revision')
}
export interface ToolResultValue {
  id?: string
  source: JsonRecord
  callId: string
  content: JsonRecord[]
  isError?: boolean
  /** Extra fields already on the admitted message, never an observation envelope. */
  messageExtensions: JsonRecord
  resultExtensions: JsonRecord
}
function extras(value: JsonRecord, fields: string[]): JsonRecord {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !fields.includes(key)))
}
/** Decode only the requested dialect; mixed layouts never auto-detect successfully. */
export function decodeToolResult(message: unknown, revision: unknown,
  options: { requireIdentity?: boolean; requireError?: boolean } = {}): ToolResultValue {
  const dialect = dialectForRevision(revision)
  if (!record(message) || !record(message.source) || message.source.kind !== 'tool'
    || !nonempty(message.source.callId)) return fail('tool result requires a tool source')
  if (options.requireIdentity && !nonempty(message.id)) return fail('tool result requires a message identity')
  if ('id' in message && !nonempty(message.id)) return fail('invalid tool message identity')
  let body: JsonRecord
  if (dialect === 3) {
    if (message.role !== 'user' || 'toolCallId' in message || 'isError' in message
      || !Array.isArray(message.content) || message.content.length !== 1
      || !record(message.content[0]) || message.content[0].type !== 'tool-result') {
      return fail('V3 requires exactly one nested tool-result')
    }
    body = message.content[0]
  } else {
    if (message.role !== 'tool') return fail('V4 requires a first-class tool-role result')
    body = message
  }
  if (body.toolCallId !== message.source.callId || !Array.isArray(body.content)
    || body.content.some((block: unknown) => !record(block) || !nonempty(block.type) || block.type === 'tool-result')) {
    return fail('tool result content or correlation differs')
  }
  if (('isError' in body && typeof body.isError !== 'boolean')
    || (options.requireError && typeof body.isError !== 'boolean')) return fail('invalid tool result error flag')
  const messageFields = dialect === 3 ? ['id', 'role', 'source', 'content']
    : ['id', 'role', 'source', 'content', 'toolCallId', 'isError']
  return structuredClone({ ...('id' in message ? { id: message.id } : {}), source: message.source,
    callId: body.toolCallId, content: body.content, ...('isError' in body ? { isError: body.isError } : {}),
    messageExtensions: extras(message, messageFields),
    resultExtensions: dialect === 3 ? extras(body, ['type', 'toolCallId', 'content', 'isError']) : {} })
}
/** Encode an already validated value, with unchanged message/call identity and content. */
export function encodeToolResult(value: ToolResultValue, revision: unknown): JsonRecord {
  const dialect = dialectForRevision(revision)
  if (!nonempty(value.callId) || value.source.kind !== 'tool' || value.source.callId !== value.callId) {
    return fail('tool result identity differs')
  }
  const messageReserved = ['id', 'role', 'source', 'content', 'toolCallId', 'isError']
  const resultReserved = ['type', 'toolCallId', 'content', 'isError', ...(dialect === 4 ? ['id', 'role', 'source'] : [])]
  if (!record(value.messageExtensions) || !record(value.resultExtensions)
    || Object.keys(value.messageExtensions).some(key => messageReserved.includes(key))
    || Object.keys(value.resultExtensions).some(key => resultReserved.includes(key))) {
    return fail('tool result extension collides with a reserved field')
  }
  const base = { ...value.messageExtensions, ...('id' in value ? { id: value.id } : {}), source: value.source }
  const result = { ...value.resultExtensions, toolCallId: value.callId, content: value.content,
    ...('isError' in value ? { isError: value.isError } : {}) }
  const encoded = dialect === 3 ? { ...base, role: 'user', content: [{ ...result, type: 'tool-result' }] }
    : { ...base, ...result, role: 'tool' }
  // Protect the public constructor from forged canonical values as well.
  decodeToolResult(encoded, revision, { requireIdentity: 'id' in value })
  return structuredClone(encoded)
}
/** Strict result body used by the existing text-only registered provider consumer. */
export function toolResultText(message: unknown, revision: unknown): string {
  const value = decodeToolResult(message, revision, { requireError: true })
  if (value.content.some(block => block.type !== 'text' || typeof block.text !== 'string')) {
    return fail('configured tool result requires text blocks')
  }
  return value.content.map(block => block.text).join('')
}
const RENAMED_PRODUCERS: Record<string, string> = {
  compact: 'compact-checkpoint', 'tools-code-mode': 'ptc-mode', 'tools-ptc': 'ptc-mode',
  'dsh-compaction-basic': 'compact-basic', '@deepseek-ai/dsh-system-prompt': 'runtime-context',
}
const SAME_NAME_PRODUCERS = new Set([
  'agent-instructions', 'session-reference', 'team-message', 'goal', 'skill-invocation', 'skill-catalog',
  'coordinator', 'subagent-report', 'subagent-settled', 'webhook', 'agent-message', 'model-selection',
  'plan-mode', 'time-context', 'tmux-context', 'user-approval', 'repeat-tool-reminder', 'tool-cordis',
  'cordis-host-runner', 'tool-goal', 'tool-jobs', 'hooks-codex', 'hooks-claude-code', 'schedule',
  'dsh-session-title-llm',
])
/** Released upstream source mapping, applied only to detached historical messages. */
function projectSource(source: JsonRecord, role: string): JsonRecord {
  if (source.kind !== 'plugin') return structuredClone(source)
  if (typeof source.plugin !== 'string') return fail('V3 plugin source requires a string plugin identity')
  const plugin = source.plugin
  const kind = plugin === '@deepseek-ai/dsh-system-prompt' && role === 'system' ? 'system-prompt'
    : Object.hasOwn(RENAMED_PRODUCERS, plugin) ? RENAMED_PRODUCERS[plugin]
      : SAME_NAME_PRODUCERS.has(plugin) ? plugin : `plugin:${plugin}`
  return Object.fromEntries(Object.entries(source).filter(([key]) => key !== 'plugin')
    .map(([key, value]) => [key, key === 'kind' ? kind : structuredClone(value)]))
}
/** Read-side migration of one already selected formal message, never a Registry envelope. */
export function projectMessage(message: unknown, sourceRevision: unknown, targetRevision: unknown): JsonRecord {
  const from = dialectForRevision(sourceRevision); const to = dialectForRevision(targetRevision)
  if (from === 4 && to === 3) return fail('V4 history cannot be projected into V3')
  if (!record(message) || !nonempty(message.id) || !record(message.source) || !nonempty(message.source.kind)
    || !Array.isArray(message.content) || message.content.some((b: unknown) => !record(b) || !nonempty(b.type))) {
    return fail('history requires an identified formal message')
  }
  const isTool = message.source.kind === 'tool' || message.role === 'tool'
  let projected: JsonRecord
  if (isTool) {
    const decoded = decodeToolResult(message, sourceRevision, { requireIdentity: true })
    if (from !== to) {
      // Match official liftToolResult: preserve extensions under their original owner.
      decoded.messageExtensions = Object.fromEntries([
        ...Object.entries(decoded.messageExtensions).map(([k, v]) => [`plugin:message:${k}`, v]),
        ...Object.entries(decoded.resultExtensions).map(([k, v]) => [`plugin:result:${k}`, v]),
      ])
      decoded.resultExtensions = {}
    }
    projected = encodeToolResult(decoded, targetRevision)
  } else {
    if (!['user', 'assistant'].includes(message.role)
      || message.content.some((b: JsonRecord) => b.type === 'tool-result')
      || 'toolCallId' in message || 'isError' in message) return fail('unsupported managed historical message role or layout')
    if (message.role === 'assistant' && (message.source.kind !== 'model'
      || !nonempty(message.source.provider) || !nonempty(message.source.model))) return fail('assistant history requires model attribution')
    projected = structuredClone(message)
  }
  if (from === 3 && to === 4) projected.source = projectSource(projected.source, projected.role)
  if (to === 4 && projected.source.kind === 'plugin') return fail('V4 rejects retired plugin source syntax')
  return projected
}
export function projectHeader(header: unknown, sourceRevision: unknown, targetRevision: unknown): JsonRecord {
  const from = dialectForRevision(sourceRevision); const to = dialectForRevision(targetRevision)
  if (from === 4 && to === 3) return fail('V4 history cannot be projected into V3')
  if (!record(header) || header.version !== from || !nonempty(header.id)
    || !Number.isSafeInteger(header.createdAt) || header.createdAt < 0 || typeof header.isSeeded !== 'boolean') {
    return fail('DSH header and recorded revision differ')
  }
  if (Object.hasOwn(header, 'seedLength')
    || (header.cwd !== undefined && (typeof header.cwd !== 'string' || !isAbsolute(header.cwd)))
    || (header.parentSession !== undefined && typeof header.parentSession !== 'string')
    || (header.origin !== undefined && header.origin !== 'subagent')
    || (header.delegationDepth !== undefined && (!Number.isSafeInteger(header.delegationDepth) || header.delegationDepth < 0))
    || (header.agentPreset !== undefined && typeof header.agentPreset !== 'string')) {
    return fail('DSH header violates the pinned Session field contract')
  }
  // Existing managed profile rejects fork/delegate/preset rather than fabricating lineage.
  if (header.isSeeded || header.parentSession || header.origin || header.agentPreset) return fail('unsupported managed header lineage')
  return { ...structuredClone(header), version: to }
}
