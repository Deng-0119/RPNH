import { Context, Service } from '@deepseek-ai/cordis'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import type { Session, UserMessage } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import AgentRegistry, { agentEvents } from '@deepseek-ai/dsh-agent'
import type { AgentOptions, AgentCancelCause, InboxTarget, AgentHandle } from '@deepseek-ai/dsh-agent'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import type { AgentMachine } from '@deepseek-ai/dsh-agent-loop'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { createScope } from '@deepseek-ai/dsh-scope'
import { ReactLoopInbox } from '../packages/core/agent-loop/src/inbox.ts'
import { appendOutcome, appendTurn, projectSession, SETTLED_STATES } from './projection.ts'
import { RpcError } from './rpc.ts'
import type { Initialization, RpcPort, Selection, SessionRow } from './rpc.ts'

declare module '@deepseek-ai/cordis' { interface Context { rpnhFrontend: FrontendBinding } }
class FrontendBinding extends Service {
  constructor(ctx: Context, readonly config: { rpc: RpcPort; initial: Initialization }) { super(ctx, 'rpnhFrontend') }
}

/** The native factory owns lifecycle only. Every execution operation belongs to RPNH. */
export class MainSessionMachine implements AgentMachine {
  readonly scope
  readonly ctx: Context
  readonly inbox: ReactLoopInbox
  private activity: Promise<void> | undefined
  private maintenance = false
  private disposed = false
  private controller = new AbortController()
  private readonly seen = new Map<string, string>()
  row: SessionRow
  lastError: string | undefined
  constructor(loopCtx: Context, readonly id: SessionId, readonly options: AgentOptions, readonly session: Session) {
    this.scope = createScope(loopCtx, this); this.ctx = this.scope.ctx
    this.inbox = new ReactLoopInbox(loopCtx.sessionProjections, session, agentEvents(loopCtx, this))
    this.row = loopCtx.rpnhFrontend.config.initial.session
    for (const turn of this.row.turns) this.seen.set(`${turn.ordinal}:${turn.key}`, turn.state)
  }
  get rpc(): RpcPort { return this.ctx.rpnhFrontend.config.rpc }
  get status(): 'idle' | 'running' { return this.activity && !this.maintenance ? 'running' : 'idle' }
  async refresh(): Promise<SessionRow> {
    const row = await this.rpc.request<SessionRow>('snapshot')
    if (row.id !== this.id) throw new Error('RPNH changed the bound session identity')
    for (const turn of row.turns) {
      const key = `${turn.ordinal}:${turn.key}`
      const previous = this.seen.get(key)
      if (previous === undefined) appendTurn(this.session, row.id, turn)
      else if (previous !== turn.state && SETTLED_STATES.has(turn.state)) appendOutcome(this.session, row.id, turn)
      this.seen.set(key, turn.state)
    }
    this.row = row
    return row
  }
  private track(operation: Promise<void>): void {
    this.lastError = undefined
    this.activity = operation.catch(error => { this.lastError = String(error) }).finally(() => {
      this.activity = undefined
      agentEvents(this.ctx, this).emit('agent/status', { status: 'idle' })
    })
    agentEvents(this.ctx, this).emit('agent/status', { status: 'running' })
  }
  private assertAvailable(): void {
    if (this.disposed) throw new RpcError('closed', 'DSH frontend has closed.', 503)
    if (this.activity) throw new RpcError('busy', 'Wait for the current RPNH operation or stop it.', 409)
  }
  async submit(text: string, requestId: string, selection: Selection = {}): Promise<{ ordinal: number }> {
    this.assertAvailable()
    const admitted = this.rpc.request<{ ordinal: number }>('submit', { text, requestId, ...selection })
    this.track(admitted.then(async ({ ordinal }) => { await this.followOrdinal(ordinal) }))
    return await admitted
  }
  async followOrdinal(ordinal: number): Promise<void> {
    while (!this.disposed && !this.controller.signal.aborted) {
      const row = await this.refresh()
      const turn = row.turns.find(turn => turn.ordinal === ordinal)
      if (!turn) throw new Error(`RPNH accepted turn ${ordinal} is missing; no prompt was replayed`)
      if (SETTLED_STATES.has(turn.state)) return
      await new Promise<void>(resolve => {
        const done = (): void => { clearTimeout(timer); this.controller.signal.removeEventListener('abort', done); resolve() }
        const timer = setTimeout(done, 150)
        this.controller.signal.addEventListener('abort', done, { once: true })
      })
    }
  }
  followup(message: UserMessage): void { this.send(message, 'next-turn', true) }
  send(message: UserMessage, target: InboxTarget, wakeup: boolean): void {
    if (target !== 'next-turn' || !wakeup) throw new RpcError('unsupported', 'RPNH frontend accepts ordinary text turns; steering/injection is unavailable.')
    if (message.content.some(part => part.type !== 'text')) throw new RpcError('unsupported', 'Only text input is supported by this frontend.')
    this.assertAvailable()
    const text = message.content.map(part => (part as { text: string }).text).join('\n')
    void this.submit(text, message.id).catch(error => { this.lastError = String(error) })
  }
  steer(_message: UserMessage): void { throw new RpcError('unsupported', 'Mid-turn steering is unavailable.') }
  inject(_message: UserMessage): void { throw new RpcError('unsupported', 'Native context injection is unavailable.') }
  cancel(cause: AgentCancelCause): void {
    if (cause.kind === 'disposed') { this.disposed = true; this.controller.abort(); return }
    void this.rpc.request('abort').catch(error => { this.lastError = String(error) })
  }
  async whenIdle(): Promise<void> { while (this.activity) await this.activity }
  runMaintenance<T>(task: (signal: AbortSignal) => Promise<T>): Promise<T> {
    this.assertAvailable(); this.maintenance = true
    const operation = Promise.resolve().then(() => task(this.controller.signal))
    this.activity = operation.then(() => undefined, () => undefined).finally(() => {
      this.activity = undefined; this.maintenance = false
    })
    return operation
  }
}
class FrontendAgentLoop extends AgentLoop {
  static override inject = [...AgentLoop.inject, 'rpnhFrontend']
  protected override createMachine(ctx: Context, id: SessionId, options: AgentOptions, session: Session): AgentMachine {
    if (id !== ctx.rpnhFrontend.config.initial.session.id) throw new Error('Only the launcher-bound MainSession is available')
    return new MainSessionMachine(ctx, id, options, session)
  }
}

export async function createFrontendApplication(rpc: RpcPort): Promise<{
  ctx: Context; initial: Initialization; readonly machine: MainSessionMachine; rebuild(): Promise<void>; close(): Promise<void>
}> {
  const initial = await rpc.request<Initialization>('initialize')
  const ctx = new Context()
  let handle: AgentHandle | undefined
  const create = async (): Promise<void> => {
    handle = await ctx.agents.create({ sessionId: SessionId(initial.session.id),
      seed: projectSession(initial.session),
      agentOptions: { provider: initial.session.model.provider, model: initial.session.model.model } })
  }
  try {
    // Empty registries are native factory dependencies. No adapters, native tools,
    // filesystem, user config, persistence plugin, or ReactLoopAgent is mounted.
    await ctx.plugin(LlmRuntime); await ctx.plugin(SessionStore); await ctx.plugin(SessionProjectionRegistry)
    await ctx.plugin(SystemPrompt); await ctx.plugin(ToolRuntime); await ctx.plugin(AgentRegistry)
    await ctx.plugin(FrontendBinding, { rpc, initial }); await ctx.plugin(FrontendAgentLoop, { agents: [] })
    await create()
    return { ctx, initial,
      get machine() { return handle!.agent as MainSessionMachine },
      async rebuild() {
        await handle?.dispose()
        initial.session = await rpc.request<SessionRow>('snapshot')
        await create()
      },
      async close() { rpc.close(); await ctx.fiber.dispose() },
    }
  } catch (error) { await ctx.fiber.dispose(); throw error }
}
