/** Detached projection only. All authority and dispatch remain with the existing owner. */
import { isDeepStrictEqual } from 'node:util'
import { DshCodecError, dialectForRevision, projectHeader, projectMessage, record,
  type JsonRecord } from './message-codec.ts'
export interface DetachedHistory { header: JsonRecord; events: JsonRecord[]; sourceRevision: string }
const fail = (message: string): never => { throw new DshCodecError(message) }
function sameHeader(source: JsonRecord, expected: JsonRecord, revision: string): void {
  const checked = projectHeader(source, revision, revision)
  if (!isDeepStrictEqual(checked, expected)) fail('one managed session requires one original header')
}
/** Select only the formal slots already used by RegistryProjectionPersistence. */
export function projectTurnEvents(turnRecord: unknown, targetRevision: unknown): JsonRecord[] {
  if (!record(turnRecord) || !Number.isSafeInteger(turnRecord.ordinal) || turnRecord.ordinal < 1
    || !record(turnRecord.user_input) || !record(turnRecord.answer)) return fail('invalid committed DSH turn')
  const input = turnRecord.user_input; const output = turnRecord.answer
  const sourceRevision = input.upstream_revision
  dialectForRevision(sourceRevision); dialectForRevision(targetRevision)
  if (!Array.isArray(input.messages) || !Array.isArray(output.calls)
    || !['completed', 'denied'].includes(output.status)) return fail('unsupported committed DSH turn outcome')
  const turn = turnRecord.ordinal; const events: JsonRecord[] = []
  const emit = (type: string, data: JsonRecord, surface = false): void => {
    events.push({ type, data, ...(surface ? { surfaceOp: 'append' } : {}) })
  }
  emit('turn/start', { turn })
  for (const message of input.messages) {
    const converted = projectMessage(message, sourceRevision, targetRevision)
    if (converted.role !== 'user' || converted.source.kind === 'tool') return fail('new turn input must be a user/context message')
    emit('user/message', converted, true)
  }
  let step = 0; let open = false; let pending: JsonRecord | undefined
  const executed = new Set<string>()
  for (const call of output.calls) {
    if (!record(call) || !record(call.ticket) || !record(call.observation)) return fail('invalid committed call')
    if (call.ticket.upstream_revision !== sourceRevision) return fail('committed execution revision differs')
    const message = projectMessage(call.observation.message, sourceRevision, targetRevision)
    if (call.ticket.kind === 'model') {
      if (pending) return fail('model step follows an unresolved tool call')
      if (message.role !== 'assistant') return fail('model observation requires assistant message')
      const calls = message.content.filter((block: JsonRecord) => block.type === 'tool-call')
      if (calls.length > 1) return fail('parallel calls are outside the managed profile')
      if (calls.length) {
        const tool = calls[0]
        if (typeof tool.id !== 'string' || !tool.id || typeof tool.name !== 'string' || !tool.name
          || typeof tool.arguments !== 'string') return fail('invalid historical tool call')
        // Validation only; never reserialize the original argument string.
        let parsed: unknown
        try { parsed = JSON.parse(tool.arguments) } catch { return fail('invalid historical tool arguments') }
        if (!record(parsed)) return fail('historical tool arguments require an object')
        pending = { ...tool, parsed }
      }
      if (open) emit('step/end', { turn, step })
      step++; open = true
      emit('step/start', { turn, step })
      emit('assistant/message', { turn, step, message, stream: [] }, true)
    } else if (call.ticket.kind === 'tool') {
      if (!record(call.arguments) || !record(call.arguments.call) || !pending) return fail('tool result has no model call')
      const tool = call.arguments.call
      if (tool.id !== pending.id || tool.name !== pending.name || executed.has(tool.id)
        || typeof tool.raw_arguments !== 'string' || tool.raw_arguments !== pending.arguments
        || !isDeepStrictEqual(tool.arguments, pending.parsed)
        || message.source.kind !== 'tool' || message.source.callId !== tool.id) return fail('historical tool correlation differs')
      if (call.observation.isError !== undefined) {
        const error = message.role === 'tool' ? message.isError : message.content[0].isError
        if (typeof call.observation.isError !== 'boolean' || (error ?? false) !== call.observation.isError) return fail('historical tool error status differs')
      }
      emit('tool/call', { turn, step, callId: tool.id, name: tool.name, arguments: tool.raw_arguments })
      emit('tool/result', { turn, step, message }, true)
      executed.add(tool.id); pending = undefined
    } else return fail('unsupported committed capability kind')
  }
  // Policy denial is a valid committed suffix without a dispatched tool result.
  if (pending && output.status !== 'denied') return fail('completed turn has an unresolved tool call')
  if (open) emit('step/end', { turn, step })
  emit('turn/end', { turn, reason: { kind: output.status === 'completed' ? 'completed' : 'blocked' } })
  return events
}
/** Build and validate the entire detached view before publishing any Session events. */
export function projectHistory(history: unknown, targetRevision: unknown,
  fallback?: { header: JsonRecord; revision: string }): DetachedHistory {
  if (!record(history) || !Array.isArray(history.committed_history)) return fail('invalid Registry history view')
  const first = history.committed_history[0]?.user_input ?? history.active?.user_input
  const header = first?.header ?? fallback?.header
  const sourceRevision = first?.upstream_revision ?? fallback?.revision
  const projectedHeader = projectHeader(header, sourceRevision, targetRevision)
  if (history.session_id !== undefined && history.session_id !== projectedHeader.id) return fail('history belongs to a different session')
  const events: JsonRecord[] = []
  let ordinal = 0
  for (const item of history.committed_history) {
    if (!record(item) || !record(item.user_input) || item.user_input.upstream_revision !== sourceRevision
      || item.user_input.session_id !== header.id || item.ordinal <= ordinal) return fail('mixed or unordered DSH history')
    sameHeader(item.user_input.header, header, sourceRevision)
    ordinal = item.ordinal
    events.push(...projectTurnEvents(item, targetRevision))
  }
  if (history.active !== null && history.active !== undefined) {
    const input = history.active.user_input
    if (!record(input) || input.upstream_revision !== sourceRevision || input.session_id !== header.id) return fail('active request revision or session differs')
    sameHeader(input.header, header, sourceRevision)
  }
  return { header: projectedHeader, sourceRevision, events: events.map((event, seq) => ({
    seq, time: projectedHeader.createdAt + seq, ...event,
  })) }
}
/** Execution admission is separate from read migration; no cross-revision writes. */
export function assertExecutionRevision(history: unknown, executionRevision: unknown): void {
  dialectForRevision(executionRevision)
  if (!record(history) || !Array.isArray(history.committed_history)) return fail('invalid Registry history view')
  const requests = [...history.committed_history.map((item: JsonRecord) => item?.user_input),
    ...(history.active ? [history.active.user_input] : [])]
  for (const request of requests) {
    if (!record(request) || request.upstream_revision !== executionRevision) {
      return fail('use the original supported DSH runtime for this session; cross-revision execution is not admitted')
    }
  }
}
