import { Context } from '@deepseek-ai/cordis'
import { TypertRemoteService, RemoteError } from '@deepseek-ai/dsh-typert-protocol'
import { randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { officialModelCatalog, officialRecords, officialProjections, officialSummary, officialHeader, officialSelection } from './official_projection.ts'
import { isActive } from './projection.ts'
import { RpcError } from './rpc.ts'
import type { Initialization, Profile, RpcPort, SessionRow } from './rpc.ts'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
// Upstream pure protocol helpers; this does not install a DSH execution owner.
import { DEFAULT_MAX_MESSAGES, paginate, pageRecords, validateHistoryWindow } from '../packages/api/session-controller/src/history.ts'
import { FRONTEND_DIR, DESCRIPTOR_PACKAGES, COMPATIBILITY } from './setup.ts'
import { projectionFrames } from './projection_frames.ts'

declare module '@deepseek-ai/dsh-typert-protocol' {
  interface RemoteErrorDetailsMap {
    'rpnh/unavailable': null
    'rpnh/history-changed': null
    'rpnh/session': null
    'rpnh/busy': null
    'rpnh/model': null
    'rpnh/effort': null
    'rpnh/bound-session': null
    'rpnh/command': null
  }
}

export const RPNH_COMMANDS = ['rpnh-help', 'rpnh-tasks', 'rpnh-task', 'rpnh-net', 'rpnh-resume', 'rpnh-rollback', 'rpnh-agent', 'rpnh-workflow', 'rpnh-send']
const unavailable = () => { throw new RemoteError('rpnh/unavailable', 'This capability is unavailable in the bound RPNH frontend.', null) }

/** Small per-subscriber queue. These are live UI frames, never durable authority. */
class Frames {
  private items: any[] = []
  private wake: (() => void) | undefined
  private closed = false
  push(value: any) { if (!this.closed) { this.items.push(value); this.wake?.() } }
  close() { this.closed = true; this.wake?.() }
  async *read(signal: AbortSignal) {
    const stop = () => this.close()
    signal.addEventListener('abort', stop, { once: true })
    try {
      while (!this.closed && !signal.aborted) {
        if (this.items.length) yield this.items.shift()
        else await new Promise<void>(done => { this.wake = done })
      }
    } finally { signal.removeEventListener('abort', stop); this.wake = undefined }
  }
}

/** Official DSH Remote contracts over exactly one Python-owned MainSession. */
export class OfficialView {
  readonly id: string
  row: SessionRow
  private journal: any[] = []
  private sourceCount = 0
  private readonly followers = new Set<Frames>()
  private readonly controllers = new Set<Frames>()
  private readonly projectionObservers = new Set<Frames>()
  private readonly idleStreams = new Set<Frames>()
  private refreshing: Promise<void> | undefined
  private timer: ReturnType<typeof setTimeout> | undefined
  private closed = false
  private mutation = false
  readonly workspace
  constructor(readonly ctx: Context, readonly rpc: RpcPort, readonly initial: Initialization, readonly cwd: string) {
    this.row = initial.session; this.id = this.row.id
    this.workspace = { workspaceId: 'rpnh-bound-workspace', path: cwd, title: 'RPNH', sessionIds: [this.id],
      createdAt: new Date(this.row.created).toISOString(), updatedAt: new Date(this.row.updated).toISOString() }
    this.mergeHistory()
  }
  get cursor() { return this.journal.length - 1 }
  private values() { return { ...officialProjections(this.row), workingDirectory: this.cwd,
    inbox: { 'next-turn': [], 'next-step': [] } } }
  private baseline() { return { asOfSeq: this.cursor, values: this.values() } }
  summary() { return { ...officialSummary(this.row, this.cwd), projections: { kind: 'sequenced', ...this.baseline() } } }
  private append(event: any) {
    const projected = { ...event, seq: this.journal.length }
    this.journal.push({ type: 'event', event: projected })
    for (const follower of this.followers) follower.push({ type: 'event', event: projected })
  }
  private present(type: string, data: unknown) { this.append({ type, data, time: Date.now() }) }
  private mergeHistory() {
    const records = officialRecords(this.row)
    if (records.length < this.sourceCount) throw new RemoteError('rpnh/history-changed', 'The RPNH history changed; reopen the frontend to refresh its view.', null)
    for (let index = 0; index < records.length; index++) {
      const event = records[index]!.event as SessionEvent
      if (index < this.sourceCount) continue
      // Native live assistant nodes require an explicit step start; cold history
      // can reconstruct a fallback node, but a live update cannot start one.
      if (event.type === 'assistant/message') this.append({ type: 'step/start',
        time: this.row.turns.find(turn => turn.ordinal === event.data.turn)?.created ?? event.time,
        data: { turn: event.data.turn, step: event.data.step } })
      this.append(event)
      if (event.type === 'assistant/message') this.append({ type: 'step/end', time: event.time,
        data: { turn: event.data.turn, step: event.data.step } })
    }
    this.sourceCount = records.length
  }
  private publish() {
    for (const frame of projectionFrames(COMPATIBILITY.version, this.cursor, this.values())) {
      for (const observer of this.projectionObservers) observer.push(frame)
      for (const controller of this.controllers) controller.push({ ...frame, sessionId: this.id })
    }
    this.ctx.emit('api-session/added', this.summary() as any)
    this.ctx.emit('api-session/status', this.id as any, isActive(this.row))
  }
  private publicNotice(): string | undefined {
    const messages: string[] = []
    if (this.row.error != null) messages.push(typeof this.row.error === 'string'
      ? this.row.error : JSON.stringify(this.row.error))
    if (this.row.turns.at(-1)?.state === 'stopped_by_owner') {
      messages.push('Paused at the RPNH checkpoint. Use /rpnh-resume or /rpnh-rollback.')
    }
    return messages.length ? messages.join('\n') : undefined
  }
  private publishNotice() {
    const message = this.publicNotice()
    this.ctx.emit('api-session/error', this.id as any, message ?? '')
  }
  private acceptRow(row: SessionRow) {
    if (row.id !== this.id) throw new RemoteError('rpnh/session', 'The bound session identity changed.', null)
    const oldCursor = this.cursor; const oldState = this.row.turns.at(-1)?.state; const oldNotice = this.publicNotice()
    const oldSelection = officialSelection(this.row.model)
    this.row = row; this.initial.session = row; this.mergeHistory()
    const selected = officialSelection(row.model)
    if (selected.model !== oldSelection.model || selected.reasoningEffort !== oldSelection.reasoningEffort) {
      this.present('model/selection', selected)
    }
    if (this.cursor !== oldCursor || row.turns.at(-1)?.state !== oldState) this.publish()
    if (this.publicNotice() !== oldNotice) this.publishNotice()
  }
  refresh(): Promise<void> {
    return this.refreshing ??= this.rpc.request<SessionRow>('snapshot')
      .then(row => this.acceptRow(row)).finally(() => { this.refreshing = undefined })
  }
  start() {
    const poll = async () => {
      if (this.closed) return
      try { await this.refresh() }
      catch (error) { if (!this.closed) this.ctx.emit('api-session/error', this.id as any, String(error)) }
      if (!this.closed) this.timer = setTimeout(poll, 250)
    }
    this.timer = setTimeout(poll, 250)
  }
  close() { this.closed = true; clearTimeout(this.timer); for (const queue of [...this.followers, ...this.controllers, ...this.projectionObservers, ...this.idleStreams]) queue.close() }
  private bound(request: any) {
    const address = typeof request === 'object' && request !== null ? request.address : undefined
    const identity = typeof request === 'string' ? request : request?.sessionId ?? address?.sessionId ?? request?.id
    if (identity !== this.id || (address !== undefined && (address?.kind !== 'session' || address.sessionId !== this.id))) {
      throw new RemoteError('rpnh/session', 'Only the launcher-bound RPNH MainSession is available.', null)
    }
  }
  private history(request: any) {
    this.bound(request)
    if (request.throughSeq !== undefined && (!Number.isSafeInteger(request.throughSeq)
      || request.throughSeq < -1 || Object.is(request.throughSeq, -0))) {
      throw new RemoteError('gateway/bad-request', 'throughSeq must be a safe integer greater than or equal to -1.', {})
    }
    if (request.beforeSeq !== undefined && (!Number.isSafeInteger(request.beforeSeq)
      || request.beforeSeq < 0 || Object.is(request.beforeSeq, -0))) {
      throw new RemoteError('gateway/bad-request', 'beforeSeq must be a non-negative safe integer.', {})
    }
    if (request.turnWindow !== undefined && (request.turnWindow === null
      || typeof request.turnWindow !== 'object' || Array.isArray(request.turnWindow))) {
      throw new RemoteError('gateway/bad-request', 'turnWindow must contain minMessages and minTurns.', {})
    }
    validateHistoryWindow(request)
    const cursor = request.throughSeq === undefined ? this.cursor : request.throughSeq
    if (cursor > this.cursor) throw new RemoteError('gateway/bad-request', 'The requested history cut exceeds the observed RPNH history.', {})
    const page = paginate(this.journal.map(record => record.event as SessionEvent),
      request.beforeSeq, request.maxMessages ?? DEFAULT_MAX_MESSAGES, cursor, request.turnWindow)
    return { cursor, records: pageRecords(page.events), hasMore: page.hasMore }
  }
  private selection() {
    const selected = officialSelection(this.row.model)
    return { selectionId: selected.model, ...(selected.reasoningEffort === undefined ? {} : { reasoningEffort: selected.reasoningEffort }) }
  }
  private async mutate<T>(operation: () => Promise<T>): Promise<T> {
    if (this.mutation) throw new RemoteError('rpnh/busy', 'Another operation is being admitted.', null)
    this.mutation = true
    try { return await operation() } finally { this.mutation = false }
  }
  handlers(): Record<string, Record<string, (...args: any[]) => any>> {
    const view = this
    return {
      session: {
        list: async () => { await view.refresh(); return { items: [view.summary()] } },
        projections: request => { view.bound(request); return { kind: 'sequenced', ...view.baseline() } },
        page: request => view.history(request),
        events: request => view.history(request),
        async *follow(request, signal) {
          if (signal.aborted) return
          if (request.throughSeq !== undefined || request.beforeSeq !== undefined) {
            throw new RemoteError('gateway/bad-request', 'follow opens the current history window; use page or events for an exact cut.', {})
          }
          // One synchronous read/subscription boundary: increments after this
          // cut are queued before the first snapshot is delivered.
          const page = view.history(request)
          const queue = new Frames(); view.followers.add(queue)
          try {
            yield { type: 'snapshot', header: officialHeader(view.row, view.cwd), ...page,
              projections: view.baseline(),
              ...(request.assistantStream === true ? { assistantStream: { revision: 0 } } : {}) }
            view.publishNotice()
            yield* queue.read(signal)
          } finally { view.followers.delete(queue); queue.close() }
        },
        async *observeEvent(request, signal) {
          view.bound(request)
          if (signal.aborted) return
          const queue = new Frames(); view.followers.add(queue)
          try { yield { type: 'ready' }; yield* queue.read(signal) }
          finally { view.followers.delete(queue); queue.close() }
        },
        // Current presentation values at each RPNH observation boundary;
        // this stream does not provide a per-commit projection audit log.
        async *observeProjections(request, signal) {
          view.bound(request)
          if (signal.aborted) return
          const queue = new Frames(); view.projectionObservers.add(queue)
          try { yield { type: 'ready' }; yield* queue.read(signal) }
          finally { view.projectionObservers.delete(queue); queue.close() }
        },
        async *control(signal) {
          const queue = new Frames(); view.controllers.add(queue)
          try { yield { type: 'baseline', value: { projections: { [view.id]: view.baseline() } } }; yield* queue.read(signal) }
          finally { view.controllers.delete(queue); queue.close() }
        },
        modelCatalog: () => officialModelCatalog(view.initial),
        async selectModel(request) {
          view.bound(request)
          const profile = view.initial.configuration.profiles.find(profile => profile.selection === request.model && profile.ready)
          if (request.provider !== 'rpnh' || !profile) throw new RemoteError('rpnh/model', 'Choose an available RPNH model profile.', null)
          const effort = request.reasoningEffort ?? profile.default_reasoning_effort
          if (effort !== null && !profile.supported_reasoning_efforts.includes(effort)) throw new RemoteError('rpnh/effort', 'Unsupported reasoning effort.', null)
          return await view.mutate(async () => {
            // Settle any older read before asking the same serialized RPNH
            // owner to persist its selection. Nothing is published optimistically.
            await view.refresh()
            let selected: Profile
            try {
              selected = await view.rpc.request<Profile>('select_model', {
                selectionId: profile.selection, ...(effort === null ? {} : { reasoningEffort: effort }),
              })
            } catch (error) {
              if (!(error instanceof RpcError)) throw error
              throw new RemoteError('session/model-unavailable', `${error.code}: ${error.message}`,
                { provider: request.provider, model: request.model })
            }
            view.acceptRow({ ...view.row, model: selected })
            return { selected: officialSelection(selected) }
          })
        },
        create() {
          if (view.row.turns.length) throw new RemoteError('rpnh/bound-session', 'This frontend is bound to an existing RPNH MainSession. Start a new RPNH session from the launcher.', null)
          return { sessionId: view.id }
        },
        canOpenWorkspacePath: () => false,
        async prompt(request) {
          view.bound(request)
          if (!Array.isArray(request.content) || typeof request.requestId !== 'string'
            || !/^[A-Za-z0-9._:-]{1,160}$/.test(request.requestId)) {
            throw new RemoteError('gateway/bad-request', 'A text prompt and valid requestId are required.', {})
          }
          if (request.mode !== 'queue' || request.content.some((part: any) => part?.type !== 'text')) unavailable()
          if (request.content.some((part: any) => typeof part.text !== 'string')) {
            throw new RemoteError('gateway/bad-request', 'Text prompt parts must contain strings.', {})
          }
          const text = request.content.map((part: any) => part.text).join('\n')
          if (!text.trim()) throw new RemoteError('gateway/bad-request', 'A nonempty text prompt is required.', {})
          if (/^\s*\/rpnh-/.test(text)) throw new RemoteError('rpnh/command', 'Use the native slash-command menu for RPNH commands.', null)
          await view.mutate(async () => {
            // RPNH owns admission and retry identity. The accepted key in its
            // snapshot also reconciles optimistic messages after a cold reopen.
            await view.rpc.request('submit', { text, requestId: request.requestId, ...view.selection() })
            await view.refresh()
          })
          return { accepted: true }
        },
        async cancel(request) { view.bound(request); await view.rpc.request('abort'); await view.refresh(); return { accepted: true } },
      },
      workspace: {
        async *follow(signal) { yield { type: 'baseline', value: { items: [view.workspace], archivedSessionIds: [], pinnedSessionIds: [] } }
          const queue = new Frames(); view.idleStreams.add(queue)
          try { yield* queue.read(signal) } finally { view.idleStreams.delete(queue); queue.close() } },
        initializeDefault: () => ({ workspace: view.workspace }),
      },
      settings: { describe: () => ({ writable: false, hasDocument: false, namespaces: [] }) },
      commands: {
        list(agent) { view.bound(agent); return RPNH_COMMANDS.map(name => ({ name, description: `RPNH ${name.slice(5)}`,
          ...['rpnh-help', 'rpnh-tasks', 'rpnh-resume', 'rpnh-rollback'].includes(name) ? {} : { input: { hint: 'Arguments' } } })) },
        async execute(agent, line, attachments, signal) {
          view.bound(agent)
          if (attachments.length || signal.aborted) unavailable()
          const parsed = line.match(/^\s*\/?(rpnh-[\w-]+)(?:\s+([\s\S]*))?$/)
          if (!parsed || !RPNH_COMMANDS.includes(parsed[1])) throw new RemoteError('rpnh/command', 'Unsupported RPNH command.', null)
          const commandId = randomUUID()
          return await view.mutate(async () => {
            view.present('command/run', { commandId, name: parsed[1], args: parsed[2] ?? '', source: { kind: 'user' } })
            let result
            try {
              const value = await view.rpc.request('command', { name: parsed[1], arguments: parsed[2] ?? '', requestId: commandId, ...view.selection() })
              await view.refresh()
              result = { kind: 'success', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
            } catch (error) { result = { kind: 'error', text: String(error) } }
            view.present('command/done', { commandId, ...result }); view.publish()
            return { commandId, result }
          })
        },
      },
      directoryPicker: {}, subagents: {}, credentials: {},
      fileReferences: { list: () => [] }, skills: { list: () => [] },
    }
  }
}

export async function installOfficialBff(ctx: Context, rpc: RpcPort, cwd: string) {
  const initial = await rpc.request<Initialization>('initialize')
  const view = new OfficialView(ctx, rpc, initial, cwd)
  const agent = { id: view.id, ctx }
  ctx.typert.lookups.register('agent', { parameter: 'agent', wire: 'agentId', resource: 'session',
    hostTypeSymbol: '@deepseek-ai/dsh-agent#Agent', wireTypeSymbol: '@deepseek-ai/dsh-session/types#SessionId',
    resolve: (sid: string) => sid === view.id ? agent as any : undefined })
  ctx.typert.contexts.registerHost('agent', { wire: 'agentId', resource: 'session',
    wireTypeSymbol: '@deepseek-ai/dsh-session/types#SessionId',
    resolve: (sid: string) => sid === view.id ? ctx : undefined })
  const handlers = view.handlers()
  const groups = new Map<string, { namespace: string; methods: any[] }>()
  for (const name of DESCRIPTOR_PACKAGES) {
    const artifact = await import(pathToFileURL(resolve(FRONTEND_DIR, 'generated', name + '.ts')).href)
    ctx.typert.register(artifact.TYPERT)
    for (const descriptor of artifact.TYPERT.invocations) {
      if (descriptor.namespace === 'remoteAccess') continue
      const group = groups.get(descriptor.service) ?? { namespace: descriptor.namespace, methods: [] as any[] }
      group.methods.push(descriptor); groups.set(descriptor.service, group)
    }
  }
  for (const [key, group] of groups) {
    class BoundService extends TypertRemoteService {
      constructor(context: Context) {
        super(context, key, { namespace: group.namespace })
        for (const descriptor of group.methods) Object.defineProperty(this, descriptor.implementation ?? descriptor.method, {
          configurable: true, writable: true, value: handlers[group.namespace]?.[descriptor.method] ?? unavailable,
        })
      }
    }
    new BoundService(ctx)
  }
  return view
}
