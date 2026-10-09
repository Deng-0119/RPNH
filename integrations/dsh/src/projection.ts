import { isDeepStrictEqual } from 'node:util'
import { Context } from '@deepseek-ai/cordis'
import { Session, SessionId, SessionLogOffset } from '@deepseek-ai/dsh-session'
import type { SessionHeader, SessionEvent } from '@deepseek-ai/dsh-session'
import { SessionPersistence, SessionPersistenceNotFoundError, SessionAlreadyOwnedError,
  SessionHandleClosedError, SessionReadOnlyError, SessionPersistenceRevision } from '@deepseek-ai/dsh-session-persistence'
import type { SessionHandle, SessionAccess } from '@deepseek-ai/dsh-session-persistence'
import { REVISION, type JsonRecord } from './bridge.ts'
import { projectHistory, projectTurnEvents } from './history-codec.ts'

/** Only fully settled main-thread turns become the DSH conversation surface. */
export function appendTurn(session: Session, record: JsonRecord): void {
  // Fully validate this detached turn before touching the live Session.
  for (const event of projectTurnEvents(record, REVISION)) {
    session.append(event.type as any, event.data as any,
      event.surfaceOp === 'append' ? { surfaceOp: 'append' } : undefined)
  }
}
export function project(history: JsonRecord, header: SessionHeader): SessionEvent[] {
  const view = projectHistory(history, REVISION, { header, revision: REVISION })
  const detached = Session.create(header.id, undefined, view.header as SessionHeader)
  for (const event of view.events) {
    detached.append(event.type as any, event.data as any,
      event.surfaceOp === 'append' ? { surfaceOp: 'append' } : undefined)
  }
  return detached.snapshotEvents().map((e, seq) => ({ ...e, time: view.header.createdAt + seq }))
}

export class RegistryProjectionPersistence extends SessionPersistence {
  static inject = ['rpnhBridge']
  private headers = new Map<string, SessionHeader>()
  private writers = new Set<string>()
  constructor(ctx: Context) {
    super(ctx)
    ctx.on('session/flush', async session => {
      const history = await ctx.rpnhBridge.client(session.id, false).request('history')
      if (history.active) throw new Error('Registry request is not terminal; DSH flush cannot claim success')
      // Only the inbox (unaccepted pending input) and the replay cut are local.
      // Compare every formal event, not just assistant text: a forged user/tool
      // message or terminal marker must never become a successful checkpoint.
      const semantic = (events: readonly SessionEvent[]) => events
        .filter(e => e.type !== 'agent/inbox/spliced' && e.type !== 'session/end-seed')
        .map(({ seq: _seq, time: _time, ...event }) => event)
      if (!isDeepStrictEqual(semantic(session.snapshotEvents()), semantic(project(history, session.header)))) {
        throw new Error('DSH surface differs from Registry; refusing success projection')
      }
    })
  }
  async create(header: SessionHeader): Promise<SessionHandle> {
    if (header.isSeeded || header.parentSession || header.origin || header.agentPreset) throw new Error('fork/delegate/preset is not admitted by the managed profile')
    const h = structuredClone(header); this.headers.set(h.id, h)
    await this.ctx.rpnhBridge.client(h.id, true).request('history')
    return this.handle(h.id, 'write', h)
  }
  async open(id: SessionId, access: SessionAccess): Promise<SessionHandle> {
    const history = await this.ctx.rpnhBridge.client(id, false).request('history')
    const h = history.committed_history[0]?.user_input.header ?? history.active?.user_input.header ?? this.headers.get(id)
    if (!h) throw new SessionPersistenceNotFoundError(id)
    this.headers.set(id, h)
    return this.handle(id, access, h)
  }
  private handle(id: SessionId, access: SessionAccess, header: SessionHeader): SessionHandle {
    if (access === 'write' && this.writers.has(id)) throw new SessionAlreadyOwnedError(id)
    if (access === 'write') this.writers.add(id)
    let closed = false
    let readEnd: number | undefined
    const check = (operation: string, write = false): void => {
      if (closed) throw new SessionHandleClosedError(id, operation)
      if (write && access !== 'write') throw new SessionReadOnlyError(id, operation)
    }
    const close = async (): Promise<void> => { if (!closed) { closed = true; if (access === 'write') this.writers.delete(id) } }
    return { id, header, access, inheritedEventCount: SessionLogOffset(0),
      read: async (offset = 0, length) => {
        check('read'); const h = await this.ctx.rpnhBridge.client(id, false).request('history')
        const events = project(h, header)
        readEnd = events.length
        return { eventState: 'detached', events: events.slice(offset, length === undefined ? undefined : offset + length) }
      },
      append: async events => {
        check('append', true)
        if (!events.length) return
        // DSH Session.restore adds this constructor-only replay boundary after
        // a cold read. Acknowledge that cache marker, not an execution write.
        const event = events[0]
        if (events.length === 1 && readEnd !== undefined && event.seq === readEnd
          && event.type === 'session/end-seed' && isDeepStrictEqual(event.data, {})
          && Object.keys(event).every(k => ['seq', 'time', 'type', 'data'].includes(k))) {
          readEnd = undefined
          return
        }
        throw new Error('DSH cannot append authority; use registered driver operations')
      },
      flush: async () => { check('flush', true); await this.ctx.rpnhBridge.client(id, false).request('history') },
      close, [Symbol.asyncDispose]: close }
  }
  async flush(): Promise<void> { for (const c of this.ctx.rpnhBridge.clients.values()) await c.request('history') }
  async stat(id: SessionId): Promise<any> {
    const h = await this.ctx.rpnhBridge.client(id, false).request('history')
    const header = h.committed_history[0]?.user_input.header ?? h.active?.user_input.header ?? this.headers.get(id)
    if (!header) return undefined
    return { header, revision: SessionPersistenceRevision(h.thread_ref.version_id), eventCount: project(h, header).length }
  }
  async list(): Promise<any[]> {
    const values = await Promise.all([...this.ctx.rpnhBridge.clients.keys()].map(id => this.stat(SessionId(id))))
    return values.filter(Boolean)
  }
}
